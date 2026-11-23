"""One place for Claude calls, so the notebooks and run.py behave the same way."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:  # optional: lets you keep ANTHROPIC_API_KEY in a .env file (git-ignored)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

_client = None


def has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def chat(system: str, messages: list[dict], model: str = DEFAULT_MODEL,
         effort: str = "high", max_tokens: int = 16000) -> str:
    """Send a conversation, return the text of the reply.

    Uses server-side refusal fallbacks: if the model declines, the API retries on a fallback
    model inside the same call. A refusal from the whole chain raises an error.
    """
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
