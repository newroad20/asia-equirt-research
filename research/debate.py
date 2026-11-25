"""Thesis vs bear-case debate: you pitch, an AI PM attacks with cited evidence, an independent judge scores.

Code-side checks (the model's word is not trusted):
  - every evidence id the BEAR cites must exist, otherwise that rebuttal is removed and shown as rejected
  - evidence ids YOU cite in [brackets] are checked too, so you see when you cite something that isn't there
  - the judge's evidence ids are checked the same way
  - red-flag questions from the playbook that nobody addressed are added to the judge's "unresolved" list
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
                 dry_run: bool | None = None, model: str | None = None, bear_notes: str = "- (none)",
                 questions: list[dict] | None = None):
        """bear_notes: sector-specific things a skeptic should probe (playbook.bear_notes_text(pb)).
        questions: rule hits from 01 (playbook.questions); BEAR uses them, the judge checks they were answered."""
        self.company, self.ticker, self.language = company, ticker, language
        dupes = sorted({e["id"] for e in evidence if sum(x["id"] == e["id"] for x in evidence) > 1})
        if dupes:
            raise ValueError(f"Duplicate evidence ids (one would silently overwrite the other): {dupes}")
        self.evidence = {e["id"]: e["text"] for e in evidence}
        self.questions = list(questions or [])
        self.dry_run = (not has_credentials()) if dry_run is None else dry_run
        self.model = model
        self.rounds: list[dict] = []     # {"bull": str, "bull_bad_ids": [...], "bear": {...}, "rejected": [...]}
        self._messages: list[dict] = []  # raw conversation sent to the model
        self.judgement: dict | None = None
        ev_text = "\n".join(f"[{k}] {v}" for k, v in self.evidence.items())
        q_text = "\n".join(f"- ({q['level']}) [{q['evidence_id']}] {q['question']}" for q in self.questions) or "- (none)"
        self._bear_system = load_prompt("04_bear_pm.md").format(company=company, evidence=ev_text, language=language,
                                                                bear_notes=bear_notes, questions=q_text)
        self._judge_system = load_prompt("05_judge.md").format(company=company, evidence=ev_text, language=language,
                                                               questions=q_text)

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
        j["unresolved"] = (j.get("unresolved") or []) + [
            {"question": f"[자동 검증] 토론에서 다루지 않은 경고: {q['question']}", "source_to_check": "유가증권보고서·실적발표"}
            for q in self.unaddressed_red_flags()]
        self.judgement = j
        return j

    def cited_ids(self) -> set[str]:
        """Evidence ids actually used in the debate: your pitches plus the BEAR rebuttals that survived the check."""
        ids = {i for r in self.rounds for i in ID_IN_TEXT.findall(r["bull"])}
        ids |= {i for r in self.rounds for x in r["bear"].get("rebuttals", []) for i in x.get("evidence_ids") or []}
        return ids & set(self.evidence)

    def unaddressed_red_flags(self) -> list[dict]:
        used = self.cited_ids()
        return [q for q in self.questions if q["level"] == "red" and q["evidence_id"] not in used]

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

    def argument_map(self, width: int = 48) -> str:
        """Mermaid flowchart of the debate: pitch -> cited evidence <- BEAR rebuttals, judge verdicts,
        rebuttals the code rejected, and red flags nobody addressed. Renders in JupyterLab 4.1+ and on GitHub."""
        def label(s) -> str:
            s = " ".join(str(s or "").split())
            s = s if len(s) <= width else s[: width - 1] + "…"
            return '"' + s.replace('"', "#quot;").replace("[", "(").replace("]", ")") + '"'

        L = ["flowchart LR",
             "classDef bull fill:#dbe9fb,stroke:#2a78d6,color:#111",
             "classDef bear fill:#fde3d6,stroke:#eb6834,color:#111",
             "classDef ev fill:#f4f4f4,stroke:#888,color:#111",
             "classDef supported fill:#d4f2e5,stroke:#1baf7a,color:#111",
             "classDef overreach fill:#fdf0c8,stroke:#eda100,color:#111",
             "classDef unsupported fill:#fbd5d5,stroke:#d33,color:#111",
             "classDef rejected fill:#fff,stroke:#d33,stroke-dasharray:4 3,color:#777",
             "classDef flag fill:#fff,stroke:#d33,stroke-width:2px,stroke-dasharray:4 3,color:#b00"]
        ev_used: set[str] = set()

        def ev(i: str) -> str:
            ev_used.add(i)
            return f"E_{re.sub(r'[^A-Za-z0-9_]', '_', i)}"

        for n, r in enumerate(self.rounds, 1):
            b = f"B{n}"
            L.append(f"subgraph R{n}[\"Round {n}\"]")
            L.append(f"{b}[{label('BULL: ' + r['bull'])}]:::bull")
            for k, x in enumerate(r["bear"].get("rebuttals", []), 1):
                L.append(f"{b}_X{k}[{label('BEAR · ' + str(x.get('target', '')) + ': ' + str(x.get('point', '')))}]:::bear")
            for k, x in enumerate(r["rejected"], 1):
                L.append(f"{b}_J{k}[{label('걸러진 반박: ' + str(x.get('point', '')))}]:::rejected")
            L.append("end")
            for i in dict.fromkeys(ID_IN_TEXT.findall(r["bull"])):
                if i in self.evidence:
                    L.append(f"{b} -->|인용| {ev(i)}")
            for k, x in enumerate(r["bear"].get("rebuttals", []), 1):
                L.append(f"{b}_X{k} -->|반박| {b}")
                L += [f"{b}_X{k} -->|근거| {ev(i)}" for i in x.get("evidence_ids") or [] if i in self.evidence]
            for k, _ in enumerate(r["rejected"], 1):
                L.append(f"{b}_J{k} -.->|근거 없음·틀린 id| {b}")
        j = self.judgement or {}
        claims = [(s, c) for s in ("bull_claims", "bear_claims") for c in j.get(s) or []]
        if claims:
            L.append('subgraph JD["독립 심판 판정"]')
            for k, (side, c) in enumerate(claims, 1):
                v = c.get("verdict") if c.get("verdict") in ("supported", "overreach", "unsupported") else "unsupported"
                who = "BULL" if side == "bull_claims" else "BEAR"
                L.append(f"C{k}[{label(f'{who} · {v}: ' + str(c.get('claim', '')))}]:::{v}")
            L.append("end")
            for k, (_, c) in enumerate(claims, 1):
                L += [f"C{k} -.-> {ev(i)}" for i in c.get("evidence_ids") or [] if i in self.evidence]
        for k, q in enumerate(self.unaddressed_red_flags(), 1):
            L.append(f"Q{k}[{label('다루지 않은 경고: ' + q['question'])}]:::flag")
            if q["evidence_id"] in self.evidence:
                L.append(f"Q{k} -.-> {ev(q['evidence_id'])}")
        L += [f"{ev(i)}[{label(i + ': ' + self.evidence[i])}]:::ev" for i in sorted(ev_used)]
        return "\n".join(L)

    def argument_map_md(self) -> str:
        legend = ("파랑 = 내 주장, 주황 = BEAR 반박, 회색 = 근거, 점선 빨강 = 코드가 걸러낸 반박·다루지 않은 경고, "
                  "심판 판정: 초록 = 지지됨, 노랑 = 과장, 빨강 = 근거 없음")
        return "\n".join(["## 논증 지도", "", f"*{legend}*", "", "```mermaid", self.argument_map(), "```", ""])

    def to_markdown(self, view_before: str = "", view_after: str = "") -> str:
        mode = "연습 모드(가짜 응답, API 호출 없음)" if self.dry_run else "Claude"
        L = [f"# {self.company} ({self.ticker}) 투자 논거 토론", "",
             f"*{datetime.now():%Y-%m-%d %H:%M} · {mode} · 최종 투자 판단은 사람이 합니다.*", ""]
        if view_before:
            L += ["## 토론 전 내 관점", view_before, ""]
        L += ["## 토론", ""] + [self.round_md(r, n) for n, r in enumerate(self.rounds, 1)]
        if self.judgement:
            L += [self.judge_md(), ""]
        if self.rounds:
            L += [self.argument_map_md()]
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
