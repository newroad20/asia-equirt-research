"""Thesis vs bear-case debate: you pitch, an AI PM attacks with cited evidence, an independent judge scores.

Code-side checks (the model's word is not trusted):
  - every evidence id the BEAR cites must exist, otherwise that rebuttal is removed and shown as rejected
  - evidence ids YOU cite in [brackets] are checked too, so you see when you cite something that isn't there
  - the judge's evidence ids are checked the same way
"""
import json
import re
from datetime import datetime
from pathlib import Path

from .llm import ROOT, chat, has_credentials, load_prompt, parse_json

ID_IN_TEXT = re.compile(r"\[([A-Za-z0-9_\-]+)\]")


def load_filing_facts(ticker: str) -> list[dict]:
    """Facts from the newest run.py output for this ticker (ids F1, F2, ...), if any."""
    files = sorted((ROOT / "outputs").glob(f"{ticker}_*.json"))
    if not files:
        return []
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return [{"id": f["id"], "text": f"{f['statement']} {f.get('figure') or ''} ({f.get('period') or ''})".strip()}
            for f in data.get("extract", {}).get("facts", [])]


class Debate:
    def __init__(self, company: str, ticker: str, evidence: list[dict], language: str = "Korean",
                 dry_run: bool | None = None, model: str | None = None, bear_notes: str = "- (none)"):
        """bear_notes: sector-specific things a skeptic should probe (playbook.bear_notes_text(pb))."""
        self.company, self.ticker, self.language = company, ticker, language
        self.evidence = {e["id"]: e["text"] for e in evidence}
        self.dry_run = (not has_credentials()) if dry_run is None else dry_run
        self.model = model
        self.rounds: list[dict] = []     # {"bull": str, "bull_bad_ids": [...], "bear": {...}, "rejected": [...]}
        self._messages: list[dict] = []  # raw conversation sent to the model
        self.judgement: dict | None = None
        ev_text = "\n".join(f"[{k}] {v}" for k, v in self.evidence.items())
        self._bear_system = load_prompt("04_bear_pm.md").format(company=company, evidence=ev_text, language=language,
                                                                bear_notes=bear_notes)
        self._judge_system = load_prompt("05_judge.md").format(company=company, evidence=ev_text, language=language)

    # ----- model calls (real or offline fake) -----
    def _call(self, system: str, messages: list[dict]) -> str:
        if self.dry_run:
            return self._fake(system)
        kw = {"model": self.model} if self.model else {}
        return chat(system, messages, **kw)

    def _fake(self, system: str) -> str:
        ids = list(self.evidence)
        pick = [i for i in ids if i.startswith(("FCF_M", "CAPEX_REV", "GM", "PPE_TURN"))][-3:] or ids[:3]
        if system is self._judge_system:
            return json.dumps({
                "bull_claims": [{"claim": "(연습 모드) 성장 논거", "evidence_ids": pick[:1], "verdict": "overreach", "comment": "한 해 수치로 추세를 말함"}],
                "bear_claims": [{"claim": "(연습 모드) 현금흐름 악화", "evidence_ids": pick[1:2], "verdict": "supported", "comment": "수치가 직접 뒷받침"},
                                {"claim": "(연습 모드) 존재하지 않는 근거", "evidence_ids": ["FAKE_ID"], "verdict": "unsupported", "comment": "검증 테스트용"}],
                "scores": {"bull": 2, "bear": 4},
                "strongest_bull_point": "(연습 모드)", "strongest_bear_point": "(연습 모드)",
                "unresolved": [{"question": "GPU 가동률", "source_to_check": "실적발표 Q&A"}],
                "next_steps": ["유가증권보고서의 설비투자 계획 확인", "고객 계약 기간 확인", "감가상각 내용연수 확인"]}, ensure_ascii=False)
        return json.dumps({
            "rebuttals": [
                {"target": "성장성", "point": "(연습 모드) 투자가 매출보다 빨리 늘고 있다.", "evidence_ids": pick[:2]},
                {"target": "수익성", "point": "(연습 모드) 이 반박은 없는 근거를 인용해서 자동으로 걸러져야 한다.", "evidence_ids": ["MADE_UP_2099"]},
            ],
            "concede": ["(연습 모드) 매출 성장 자체는 수치로 확인된다."],
            "data_requests": ["GPU 클러스터 가동률"],
            "hardest_question": "(연습 모드) 투자금을 회수하는 시점은 언제이고, 그 전에 자금이 부족해지지 않는가?"}, ensure_ascii=False)

    # ----- debate -----
    def bull(self, pitch: str) -> dict:
        """Send your argument (cite evidence like [GM_FY2026]). Returns the checked BEAR reply."""
        bad_ids = [i for i in ID_IN_TEXT.findall(pitch) if i not in self.evidence]
        self._messages.append({"role": "user", "content": pitch})
        raw = self._call(self._bear_system, self._messages)
        self._messages.append({"role": "assistant", "content": raw})
        reply = parse_json(raw)
        kept, rejected = [], []
        for r in reply.get("rebuttals", []):
            ids = r.get("evidence_ids") or []
            (kept if ids and all(i in self.evidence for i in ids) else rejected).append(r)
        reply["rebuttals"] = kept
        rnd = {"bull": pitch, "bull_bad_ids": bad_ids, "bear": reply, "rejected": rejected}
        self.rounds.append(rnd)
        return rnd

    def judge(self) -> dict:
        if not self.rounds:
            raise ValueError("Debate has no rounds yet; call bull() first.")
        transcript = []
        for n, r in enumerate(self.rounds, 1):
            transcript.append(f"--- Round {n} ---\nBULL:\n{r['bull']}\n\nBEAR:\n{json.dumps(r['bear'], ensure_ascii=False, indent=1)}")
        msg = [{"role": "user", "content": "DEBATE TRANSCRIPT:\n\n" + "\n\n".join(transcript)}]
        j = parse_json(self._call(self._judge_system, msg))
        for side in ("bull_claims", "bear_claims"):
            j[side] = j.get(side) or []
            for c in j[side]:
                ids = c.get("evidence_ids") or []
                c["evidence_ids"] = ids
                unknown = [i for i in ids if i not in self.evidence]
                if not ids:
                    c["verdict"] = "unsupported"
                    c["comment"] = "[자동 검증] 인용한 근거가 없음. " + (c.get("comment") or "")
                elif unknown:
                    c["verdict"] = "unsupported"
                    c["comment"] = f"[자동 검증] 존재하지 않는 근거 id: {unknown}. " + (c.get("comment") or "")
        self.judgement = j
        return j

    # ----- display / save -----
    def round_md(self, r: dict, n: int) -> str:
        b = r["bear"]
        L = [f"### Round {n}", "", "**BULL (나)**", "", r["bull"], ""]
        if r["bull_bad_ids"]:
            L += [f"> ⚠️ 내가 인용한 근거 중 목록에 없는 id: {', '.join(r['bull_bad_ids'])}", ""]
        L += ["**BEAR (AI PM)**", ""]
        for x in b.get("rebuttals", []):
            ids = ", ".join(x["evidence_ids"])
            L.append(f"- **{x.get('target', '')}**: {x.get('point', '')}  \n  근거: {ids}")
            L += [f"    - `{i}` {self.evidence[i]}" for i in x["evidence_ids"]]
        if r["rejected"]:
            L += ["", f"*근거 id가 없거나 틀려서 자동으로 걸러진 반박 {len(r['rejected'])}개:*"]
            L += [f"- ~~{x.get('point', '')}~~ (인용: {x.get('evidence_ids')})" for x in r["rejected"]]
        if b.get("concede"):
            L += ["", "**인정한 부분:** " + " / ".join(b["concede"])]
        if b.get("data_requests"):
            L += ["", "**추가로 필요한 자료:** " + " / ".join(b["data_requests"])]
        L += ["", f"**가장 어려운 질문:** {b.get('hardest_question', '')}", ""]
        return "\n".join(L)

    def judge_md(self) -> str:
        j = self.judgement or {}
        if not j:
            return ""
        cell = lambda s: str(s or "").replace("|", "/").replace("\n", " ")  # keep the table intact
        scores = j.get("scores") or {}
        L = ["## 독립 심판 평가", "", f"근거 규율 점수: BULL {scores.get('bull', '-')}/5, BEAR {scores.get('bear', '-')}/5", ""]
        for side, title in (("bull_claims", "BULL 주장"), ("bear_claims", "BEAR 주장")):
            L += [f"### {title}", "| 주장 | 근거 | 판정 | 코멘트 |", "|---|---|---|---|"]
            for c in j.get(side) or []:
                L.append(f"| {cell(c.get('claim'))} | {', '.join(c.get('evidence_ids') or [])} | "
                         f"{cell(c.get('verdict'))} | {cell(c.get('comment'))} |")
            L.append("")
        L += [f"**가장 강한 BULL 논거:** {j.get('strongest_bull_point', '')}", "",
              f"**가장 강한 BEAR 논거:** {j.get('strongest_bear_point', '')}", "", "### 아직 풀리지 않은 질문"]
        L += [f"- {u.get('question', '')} → 확인할 곳: {u.get('source_to_check', '')}" for u in j.get("unresolved") or []]
        L += ["", "### 다음 리서치 단계"] + [f"{k}. {s}" for k, s in enumerate(j.get("next_steps") or [], 1)]
        return "\n".join(L)

    def to_markdown(self, view_before: str = "", view_after: str = "") -> str:
        mode = "연습 모드(가짜 응답, API 호출 없음)" if self.dry_run else "Claude API"
        L = [f"# {self.company} ({self.ticker}) 투자 논거 토론", "",
             f"*{datetime.now():%Y-%m-%d %H:%M} · {mode} · 최종 투자 판단은 사람이 합니다.*", ""]
        if view_before:
            L += ["## 토론 전 내 관점", view_before, ""]
        L += ["## 토론", ""] + [self.round_md(r, n) for n, r in enumerate(self.rounds, 1)]
        if self.judgement:
            L += [self.judge_md(), ""]
        if view_after:
            L += ["## 토론 후 내 관점 (무엇이 바뀌었고 왜)", view_after, ""]
        L += ["## 사용한 근거", ""] + [f"- `{k}` {v}" for k, v in self.evidence.items()]
        return "\n".join(L)

    def save(self, view_before: str = "", view_after: str = "") -> Path:
        out = ROOT / "outputs" / "debates"
        out.mkdir(parents=True, exist_ok=True)
        stem = f"{self.ticker}_{datetime.now():%Y%m%d_%H%M}"
        (out / f"{stem}.md").write_text(self.to_markdown(view_before, view_after), encoding="utf-8")
        (out / f"{stem}.json").write_text(json.dumps(
            {"company": self.company, "ticker": self.ticker, "dry_run": self.dry_run, "rounds": self.rounds,
             "judgement": self.judgement, "view_before": view_before, "view_after": view_after},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return out / f"{stem}.md"
