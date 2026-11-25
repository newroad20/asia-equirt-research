"""Sector playbooks: which metrics matter, which numbers raise questions, what a skeptic should probe.

Playbooks live in playbooks/<name>.yaml. Code here knows nothing about any sector; adding a sector
means adding a YAML file. A playbook may `extends:` another one:
  - metrics: parent list + child list (order kept, duplicates dropped)
  - rules: parent rules, with a child rule replacing the parent rule that has the same id
  - bear_notes: parent + child
  - universe, description, name: child wins
"""
from pathlib import Path

import pandas as pd
import yaml

from .financials import METRICS, _consecutive, period_keys

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOKS = ROOT / "playbooks"
RULE_TYPES = {"abs_change", "rise", "fall", "pct_drop", "above_yoy", "below_yoy", "above", "below", "streak_below"}


def available() -> list[str]:
    return sorted(p.stem for p in PLAYBOOKS.glob("*.yaml"))


def load(name: str) -> dict:
    path = PLAYBOOKS / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No playbook '{name}'. Available: {available()}")
    pb = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent_name = pb.get("extends")
    if parent_name:
        parent = load(parent_name)
        child_rules = {r["id"]: r for r in pb.get("rules") or []}
        rules = [child_rules.pop(r["id"], r) for r in parent["rules"]] + list(child_rules.values())
        pb = {**parent, **pb,
              "metrics": list(dict.fromkeys(parent["metrics"] + (pb.get("metrics") or []))),
              "rules": rules,
              "bear_notes": parent["bear_notes"] + (pb.get("bear_notes") or [])}
    pb.setdefault("metrics", [])
    pb.setdefault("rules", [])
    pb.setdefault("bear_notes", [])
    pb["universe"] = pb.get("universe") or {}
    _validate(pb, path)
    return pb


def _validate(pb: dict, path: Path):
    unknown = [m for m in pb["metrics"] if m not in METRICS]
    if unknown:
        raise ValueError(f"{path.name}: unknown metrics {unknown}. Known: {list(METRICS)}")
    ids = [r.get("id") for r in pb["rules"]]
    if len(ids) != len(set(ids)) or None in ids:
        raise ValueError(f"{path.name}: every rule needs a unique id, got {ids}")
    for r in pb["rules"]:
        if r.get("type") not in RULE_TYPES:
            raise ValueError(f"{path.name}: rule {r['id']} has unknown type {r.get('type')}. Use one of {sorted(RULE_TYPES)}")
        if r.get("metric") not in pb["metrics"]:
            raise ValueError(f"{path.name}: rule {r['id']} uses metric {r.get('metric')} that is not in this playbook's metrics")
        if r.get("level") not in ("red", "yellow"):
            raise ValueError(f"{path.name}: rule {r['id']} level must be red or yellow")


def bear_notes_text(pb: dict) -> str:
    return "\n".join(f"- {n}" for n in pb["bear_notes"]) or "- (none)"


# ---------- rules ----------
def _fmt(metric: str, v: float, signed: bool = False) -> str:
    unit = METRICS[metric][1]
    sign = "+" if signed or metric.endswith("_G") else ""
    return f"{v:{sign},.2f}" if unit == "x" else f"{v:{sign},.1f}"


def questions(r: pd.DataFrame, pb: dict, prefix: str = "") -> list[dict]:
    """Apply a playbook's rules to a ratio table. Each hit is a question to answer from the filings."""
    years = list(r.index)
    if not years:
        return []
    keys = period_keys(years)
    ok = _consecutive(years)
    hits = []  # (year position, rule position, question)

    def add(pos, n, rule, cur, prev=None):
        fy, m = years[pos], rule["metric"]
        fields = {"fy": fy, "cur": _fmt(m, cur), "threshold": rule["threshold"],
                  "prev": _fmt(m, prev) if prev is not None else "",
                  "delta": _fmt(m, cur - prev, signed=True) if prev is not None else ""}
        hits.append((pos, n, {"level": rule["level"], "rule": rule["id"],
                              "evidence_id": f"{prefix}{m}_{keys[fy]}",
                              "question": rule["question"].format(**fields)}))

    for n, rule in enumerate(pb["rules"]):
        s, t, kind = r[rule["metric"]], rule["threshold"], rule["type"]
        if kind in ("above", "below"):
            cur = s.iloc[-1]
            if not pd.isna(cur) and (cur >= t if kind == "above" else cur < t):
                add(len(years) - 1, n, rule, cur)
        elif kind == "streak_below":
            recent = s.dropna().tail(rule.get("years", 3))
            if len(recent) >= 2 and (recent < t).all():
                add(years.index(recent.index[-1]), n, rule, recent.iloc[-1])
        else:
            for k in range(1, len(years)):
                if not ok.iloc[k]:
                    continue
                cur, prev = s.iloc[k], s.iloc[k - 1]
                if pd.isna(cur) or (pd.isna(prev) and kind not in ("above_yoy", "below_yoy")):
                    continue
                hit = {"abs_change": lambda: abs(cur - prev) >= t,
                       "rise": lambda: cur - prev >= t,
                       "fall": lambda: cur - prev <= -t,
                       "pct_drop": lambda: prev > 0 and cur / prev - 1 <= -t,
                       "above_yoy": lambda: cur >= t,
                       "below_yoy": lambda: cur < t}[kind]()
                if hit:
                    add(k, n, rule, cur, None if pd.isna(prev) else prev)
    return [q for _, _, q in sorted(hits, key=lambda h: (h[0], h[1]))]
