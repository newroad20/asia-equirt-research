"""Asia small/mid-cap research assistant: filings text -> verified facts -> draft thesis -> skeptical review.

Pipeline (3 LLM calls, 2 automatic code checks):
  1. extract : facts with exact quotes from the source        -> code checks each quote exists in the source
  2. thesis  : thesis / bear case / catalysts citing fact ids -> code checks every cited id exists
  3. verify  : separate skeptical pass over the draft         -> flags overreach / unsupported claims

The output is a DRAFT for a human analyst. It contains no valuation data and no recommendation.

Usage:
  export ANTHROPIC_API_KEY=...            # never write the key in code or commit it
  python run.py --company "Sakura Internet" --ticker 3778 --dir inputs/3778
  python run.py --company "Sakura Internet" --ticker 3778 --dir inputs/3778 --view "my own view in a sentence"
  python run.py --dry-run --company Test --ticker 0000 --dir inputs/demo     # offline test, no API call
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_SOURCE_CHARS = 150_000  # long transcripts/filings are cut here; the report says so


# ---------- LLM call ----------
def call_llm(system: str, user: str, model: str, max_tokens: int = 8000) -> str:
    import anthropic  # imported here so --dry-run works without the package

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences or add a sentence; take the outermost {...}."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output:\n" + text[:500])
    return json.loads(text[start : end + 1])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ---------- steps ----------
def load_sources(folder: Path) -> tuple[str, list[str]]:
    files = sorted(p for p in folder.glob("*.txt"))
    if not files:
        sys.exit(f"No .txt files in {folder}. Paste filings / earnings call text into .txt files there.")
    parts, names = [], []
    for p in files:
        parts.append(f"===== FILE: {p.name} =====\n{p.read_text(encoding='utf-8')}")
        names.append(p.name)
    return "\n\n".join(parts), names


def step_extract(source: str, company: str, model: str, llm) -> dict:
    system = (HERE / "prompts/01_extract.md").read_text(encoding="utf-8")
    user = f"COMPANY: {company}\n\nSOURCE TEXT:\n{source}"
    data = parse_json(llm(system, user, model))
    src_norm = norm(source)
    kept, dropped = [], []
    for f in data.get("facts", []):
        if norm(f.get("source_quote", "")) and norm(f["source_quote"]) in src_norm:
            kept.append(f)
        else:
            dropped.append(f)
    return {"facts": kept, "dropped": dropped}


def step_thesis(facts: list, company: str, view: str | None, model: str, llm) -> dict:
    system = (HERE / "prompts/02_thesis.md").read_text(encoding="utf-8")
    slim = [{k: f[k] for k in ("id", "category", "statement", "figure", "period")} for f in facts]
    user = f"COMPANY: {company}\n\nFACTS:\n{json.dumps(slim, ensure_ascii=False, indent=1)}\n"
    user += f"\nANALYST VIEW: {view}\n" if view else "\nANALYST VIEW: (none given)\n"
    data = parse_json(llm(system, user, model))
    valid = {f["id"] for f in facts}
    bad = []
    for section in ("thesis_points", "bear_case", "catalysts"):
        for i, item in enumerate(data.get(section, [])):
            ids = item.get("fact_ids", [])
            if not ids or any(x not in valid for x in ids):
                bad.append(f"{section}[{i}] cites missing/unknown fact ids: {ids}")
    data["_id_check_failures"] = bad
    return data


def step_verify(source: str, facts: list, thesis: dict, model: str, llm) -> dict:
    system = (HERE / "prompts/03_verify.md").read_text(encoding="utf-8")
    slim = [{k: f[k] for k in ("id", "statement", "figure", "period")} for f in facts]
    case = {k: thesis.get(k) for k in ("thesis_points", "bear_case", "catalysts")}
    user = (
        f"SOURCE TEXT:\n{source}\n\nFACTS:\n{json.dumps(slim, ensure_ascii=False)}\n\n"
        f"INVESTMENT CASE:\n{json.dumps(case, ensure_ascii=False, indent=1)}"
    )
    return parse_json(llm(system, user, model))


# ---------- report ----------
def render(company, ticker, names, truncated, extract, thesis, review, view) -> str:
    facts = {f["id"]: f for f in extract["facts"]}
    verdicts = {(c["section"], c["index"]): c for c in review.get("checks", [])}
    L = [f"# {company} ({ticker}) - draft research note", ""]
    L += [f"*Generated {date.today()} by an LLM pipeline. DRAFT for analyst review: not a recommendation. "
          f"Contains no price or valuation data.*", ""]
    L += [f"Sources: {', '.join(names)}" + ("  (**source text was truncated**)" if truncated else ""), ""]
    if view:
        L += [f"Analyst view tested: {view}", ""]
    L += ["## What the market seems to believe", thesis.get("market_belief", ""), "",
          "## Why that may be wrong", thesis.get("why_possibly_wrong", ""), ""]

    def block(title, section, key):
        L.append(f"## {title}")
        for i, it in enumerate(thesis.get(section, [])):
            v = verdicts.get((section, i), {})
            tag = f" [{v['verdict']}]" if v else ""
            ids = ", ".join(it.get("fact_ids", []))
            L.append(f"- {it[key]}{tag} (facts: {ids})")
            if v and v.get("verdict") != "supported":
                L.append(f"  - reviewer: {v.get('reason', '')}")
        L.append("")

    block("Thesis", "thesis_points", "claim")
    block("Bear case", "bear_case", "claim")
    block("Catalysts", "catalysts", "event")
    for title, key in (("What would change my mind", "what_would_change_my_mind"),
                       ("Open questions", "open_questions"),
                       ("Valuation data still needed", "data_needed")):
        L.append(f"## {title}")
        L += [f"- {x}" for x in thesis.get(key, [])]
        L.append("")
    if review.get("missed_in_source"):
        L.append("## Reviewer: relevant points in the source the draft ignored")
        L += [f"- {x}" for x in review["missed_in_source"]]
        L.append("")
    if thesis.get("_id_check_failures"):
        L.append("## Automatic check failures (fix before using)")
        L += [f"- {x}" for x in thesis["_id_check_failures"]]
        L.append("")
    L.append("## Fact table (each quote verified to appear in the source)")
    L.append("| id | category | fact | period | source quote |")
    L.append("|---|---|---|---|---|")
    for f in facts.values():
        q = f["source_quote"].replace("|", "/").replace("\n", " ")
        L.append(f"| {f['id']} | {f['category']} | {f['statement']} {f.get('figure') or ''} | {f.get('period') or ''} | {q} |")
    if extract["dropped"]:
        L += ["", f"{len(extract['dropped'])} extracted facts were discarded because their quote was not found in the source."]
    return "\n".join(L)


# ---------- offline fake for --dry-run ----------
def fake_llm(system: str, user: str, model: str) -> str:
    if "ONLY to extract facts" in system:
        src = user.split("SOURCE TEXT:\n", 1)[1]
        q = norm(src)[:80]
        return json.dumps({"facts": [
            {"id": "F1", "category": "revenue_drivers", "statement": "Demo fact", "figure": None, "period": None, "source_quote": q},
            {"id": "F2", "category": "risks", "statement": "Fabricated fact", "figure": None, "period": None, "source_quote": "this text is not in the source"}]})
    if "first-draft investment case" in system:
        return json.dumps({"market_belief": "demo", "why_possibly_wrong": "demo",
                           "thesis_points": [{"claim": "demo claim", "fact_ids": ["F1"]}],
                           "bear_case": [{"claim": "ghost claim", "fact_ids": ["F9"]}],
                           "catalysts": [{"event": "demo event", "timing": "next quarter", "fact_ids": ["F1"]}],
                           "what_would_change_my_mind": ["demo"], "open_questions": ["demo"], "data_needed": ["PER/PBR, consensus"]})
    return json.dumps({"checks": [{"section": "thesis_points", "index": 0, "verdict": "overreach", "reason": "demo"}],
                       "missed_in_source": ["demo"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--dir", required=True, help="folder with .txt source files")
    ap.add_argument("--view", help="your own one-sentence view, to be stress-tested")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="offline test with a fake model")
    a = ap.parse_args()

    if not a.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (export ANTHROPIC_API_KEY=...).")
    llm = fake_llm if a.dry_run else call_llm

    source, names = load_sources(Path(a.dir))
    truncated = len(source) > MAX_SOURCE_CHARS
    source = source[:MAX_SOURCE_CHARS]

    extract = step_extract(source, a.company, a.model, llm)
    print(f"[1/3] facts kept: {len(extract['facts'])}, discarded (quote not in source): {len(extract['dropped'])}")
    thesis = step_thesis(extract["facts"], a.company, a.view, a.model, llm)
    print(f"[2/3] draft done, id-check failures: {len(thesis['_id_check_failures'])}")
    review = step_verify(source, extract["facts"], thesis, a.model, llm)
    print(f"[3/3] review done, checks: {len(review.get('checks', []))}")

    out = HERE / "outputs"
    out.mkdir(exist_ok=True)
    stem = f"{a.ticker}_{date.today()}"
    (out / f"{stem}.json").write_text(json.dumps(
        {"extract": extract, "thesis": thesis, "review": review}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{stem}.md").write_text(
        render(a.company, a.ticker, names, truncated, extract, thesis, review, a.view), encoding="utf-8")
    print(f"saved: outputs/{stem}.md and .json")


if __name__ == "__main__":
    main()
