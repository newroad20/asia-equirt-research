"""One place for Claude calls, so the notebooks and run.py behave the same way."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:  # optional: lets you keep ANTHROPIC_API_KEY in a .env file (git-ignored)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

_client = None


def _use_cli() -> bool:
    """LLM_BACKEND=claude-code: use the logged-in Claude Code CLI instead of an API key."""
    return os.environ.get("LLM_BACKEND") == "claude-code"


def _chat_cli(system: str, messages: list[dict], model: str, effort: str) -> str:
    if len(messages) == 1:
        prompt = messages[0]["content"]
    else:  # the CLI takes one prompt, so earlier turns go in as a transcript
        turns = "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages)
        prompt = f"Conversation so far. Reply to the last user turn, as the assistant.\n\n{turns}"
    # without the keys the CLI uses the Claude login, never API billing
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    # no tools, no user settings, empty folder: the model sees only the prompt and the evidence
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(
            ["claude", "-p", "--safe-mode", "--strict-mcp-config", "--tools", "", "--no-session-persistence",
             "--system-prompt", system, "--model", model, "--effort", effort, "--output-format", "json"],
            input=prompt, capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd, timeout=900)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {}
    if p.returncode or out.get("is_error") or not out.get("result") or out.get("stop_reason") in ("refusal", "max_tokens"):
        detail = out.get("result") or out.get("stop_reason") or p.stderr or p.stdout
        raise RuntimeError(f"Claude Code call failed: {str(detail)[:500]}")
    return out["result"]


def has_credentials() -> bool:
    if _use_cli():
        return shutil.which("claude") is not None
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def chat(system: str, messages: list[dict], model: str = DEFAULT_MODEL,
         effort: str = "high", max_tokens: int = 16000) -> str:
    """Send a conversation, return the text of the reply.

    Uses server-side refusal fallbacks: if the model declines, the API retries on a fallback
    model inside the same call. A refusal from the whole chain raises an error.
    """
    if _use_cli():
        return _chat_cli(system, messages, model, effort)  # max_tokens is not configurable here
    global _client
    if _client is None:
        from .net import anthropic_client

        _client = anthropic_client()
    resp = _client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Model declined the request: {resp.stop_details}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Reply was cut off at max_tokens; raise max_tokens and retry.")
    return "".join(b.text for b in resp.content if b.type == "text")


def call_llm(system: str, user: str, model: str = DEFAULT_MODEL) -> str:
    """Single-turn helper with the signature run.py uses."""
    return chat(system, [{"role": "user", "content": user}], model=model)


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences or add a sentence; take the outermost {...}."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output:\n" + text[:500])
    return json.loads(text[start : end + 1])


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8")
