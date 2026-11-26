"""Regression tests for the 2026-11-23 code review. Offline: builds statements by hand, no network.

Run: python -m pytest tests -q
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research import financials as fin  # noqa: E402
from research.debate import Debate  # noqa: E402


def make_data(dates, revenue=None, fcf=None, balance_only_first=False):
    """Minimal yfinance-shaped statements (rows = fiscal year end)."""
    idx = pd.to_datetime(dates)
    n = len(idx)
    rev = np.array(revenue if revenue is not None else [100.0 * (1.2 ** k) for k in range(n)], dtype=float)
    if balance_only_first:
        rev[0] = np.nan
    income = pd.DataFrame({
        "Total Revenue": rev, "Gross Profit": rev * 0.3, "Operating Income": rev * 0.1,
        "EBITDA": rev * 0.25, "Net Income": rev * 0.06, "Pretax Income": rev * 0.08,
        "Tax Provision": rev * 0.02, "Interest Expense": [2.0] * n, "Reconciled Depreciation": rev * 0.15,
    }, index=idx)
    if balance_only_first:
        income.iloc[0] = np.nan
    balance = pd.DataFrame({
        "Total Assets": [500.0 + 50 * k for k in range(n)], "Stockholders Equity": [200.0 + 10 * k for k in range(n)],
        "Total Debt": [100.0] * n, "Cash And Cash Equivalents": [50.0] * n, "Net PPE": [150.0 + 30 * k for k in range(n)],
        "Invested Capital": [300.0 + 20 * k for k in range(n)], "Ordinary Shares Number": [10.0] * n,
    }, index=idx)
    cashflow = pd.DataFrame({
        "Operating Cash Flow": [30.0] * n, "Capital Expenditure": [-40.0] * n,
        "Free Cash Flow": fcf if fcf is not None else [-10.0] * n,
    }, index=idx)
    return {"income": income, "balance": balance, "cashflow": cashflow, "info": {"shortName": "Test"}}


# ---------- HIGH ----------
def test_evidence_ids_unique_when_fiscal_year_end_changes():
    # FY ending Mar-2024, then the company moves to December: Dec-2024 (9 months later)
    d = make_data(["2023-03-31", "2024-03-31", "2024-12-31", "2025-12-31"])
    r = fin.ratios(fin.line_items(d))
    ev = fin.evidence(r)
    ids = [e["id"] for e in ev]
    assert len(ids) == len(set(ids))
    assert "GM_FY2024M03" in ids and "GM_FY2024M12" in ids and "GM_FY2025" in ids
    # questions must point at ids that exist
    assert {q["evidence_id"] for q in fin.questions(r)} <= set(ids)


def _debate():
    return Debate("Test", "TEST", [{"id": "GM_FY2026", "text": "GM 30%"}], dry_run=True)


def _judge_with(d, payload):
    d.rounds.append({"bull": "x", "bull_bad_ids": [], "bear": {}, "rejected": []})
    d._fake = lambda system: json.dumps(payload)
    return d.judge()


def test_judge_handles_null_evidence_ids():
    j = _judge_with(_debate(), {"bull_claims": [{"claim": "a", "evidence_ids": None, "verdict": "supported"}],
                                "bear_claims": None})
    assert j["bull_claims"][0]["verdict"] == "unsupported"
    assert j["bear_claims"] == []


def test_judge_rejects_claim_without_citation():
    d = _debate()
    j = _judge_with(d, {"bull_claims": [{"claim": "a", "evidence_ids": [], "verdict": "supported"},
                                        {"claim": "b", "evidence_ids": ["GM_FY2026"], "verdict": "supported"},
                                        {"claim": "c", "evidence_ids": ["NOPE"], "verdict": "supported"}]})
    assert [c["verdict"] for c in j["bull_claims"]] == ["unsupported", "supported", "unsupported"]
    d.judge_md()  # renders without KeyError


# ---------- MEDIUM ----------
def test_no_revenue_raises_clear_error_and_questions_handle_empty():
    d = make_data(["2023-03-31", "2024-03-31"], revenue=[np.nan, np.nan])
    with pytest.raises(ValueError):
        fin.line_items(d)
    assert fin.questions(pd.DataFrame(columns=list(fin.METRICS))) == []


def test_peer_table_skips_bad_ticker(monkeypatch):
    good, bad = make_data(["2024-03-31", "2025-03-31"]), make_data(["2024-03-31"], revenue=[np.nan])
    monkeypatch.setattr(fin, "fetch", lambda t, refresh=False: good if t == "GOOD" else bad)
    table = fin.peer_table(["BAD", "GOOD"])
    assert list(table.index) == ["Test (GOOD)"]


def test_first_income_year_keeps_roe_from_balance_only_year():
    # Yahoo often has one more balance-sheet year than income-statement years
    d = make_data(["2022-03-31", "2023-03-31", "2024-03-31"], balance_only_first=True)
    r = fin.ratios(fin.line_items(d))
    assert list(r.index) == ["FY2023(03)", "FY2024(03)"]
    assert not np.isnan(r.at["FY2023(03)", "ROE"])


def test_fcf_filled_per_year():
    d = make_data(["2023-03-31", "2024-03-31"], fcf=[np.nan, -5.0])
    items = fin.line_items(d)
    assert items["fcf"].tolist() == [-10.0, -5.0]  # 30 - 40 filled, reported value kept


def test_questions_use_prefix():
    d = make_data(["2023-03-31", "2024-03-31", "2025-03-31"])
    r = fin.ratios(fin.line_items(d))
    ids = {e["id"] for e in fin.evidence(r, prefix="S_")}
    qs = fin.questions(r, prefix="S_")
    assert qs and {q["evidence_id"] for q in qs} <= ids


# ---------- LOW ----------
def test_growth_not_computed_across_missing_year():
    d = make_data(["2022-03-31", "2023-03-31", "2025-03-31"])  # FY2024 missing
    r = fin.ratios(fin.line_items(d))
    assert np.isnan(r.at["FY2025(03)", "REV_G"]) and np.isnan(r.at["FY2025(03)", "ROE"])
    assert not np.isnan(r.at["FY2023(03)", "REV_G"])


def test_markdown_survives_missing_fields():
    d = _debate()
    d.rounds.append({"bull": "x", "bull_bad_ids": [], "rejected": [{"evidence_ids": None}],
                     "bear": {"rebuttals": [{"evidence_ids": ["GM_FY2026"]}]}})
    assert "GM_FY2026" in d.round_md(d.rounds[0], 1)


# ---------- playbooks ----------
from research import playbook as pbmod  # noqa: E402


def test_neocloud_extends_generic_and_overrides_by_id():
    gen, neo = pbmod.load("generic"), pbmod.load("neocloud")
    gen_ids = [r["id"] for r in gen["rules"]]
    neo_ids = [r["id"] for r in neo["rules"]]
    assert neo_ids[: len(gen_ids)] == gen_ids                      # parent rules kept, in order
    assert {"depreciation_load", "asset_turn_drop", "capex_intensity"} <= set(neo_ids) - set(gen_ids)
    gm = {r["id"]: r for r in neo["rules"]}["gm_swing"]
    assert "감가상각" in gm["question"]                              # child wording replaced parent
    assert set(gen["metrics"]) < set(neo["metrics"]) and "PPE_TURN" not in gen["metrics"]
    assert len(neo["bear_notes"]) > len(gen["bear_notes"]) and neo["universe"]


def test_semiconductor_inventory_days_rule():
    d = make_data(["2023-12-31", "2024-12-31"], revenue=[100.0, 100.0])  # cost of sales = 70
    d["balance"]["Inventory"] = [7.0, 14.0]
    r = fin.ratios(fin.line_items(d))
    assert r["INV_DAYS"].tolist() == pytest.approx([36.5, 73.0])
    ev = {e["id"]: e["text"] for e in fin.evidence(r, "KRW", metrics=pbmod.load("semiconductor")["metrics"])}
    assert "73일" in ev["INV_DAYS_FY2024"]
    hits = [q for q in fin.questions(r, playbook="semiconductor") if q["rule"] == "inventory_build"]
    assert [q["evidence_id"] for q in hits] == ["INV_DAYS_FY2024"]
    d["balance"] = d["balance"].drop(columns="Inventory")  # no inventory reported: no evidence, no question
    r = fin.ratios(fin.line_items(d))
    assert r["INV_DAYS"].isna().all()
    assert not any(q["rule"] == "inventory_build" for q in fin.questions(r, playbook="semiconductor"))


def test_every_playbook_loads_and_validates():
    for name in pbmod.available():
        pbmod.load(name)


def test_playbook_validation_rejects_bad_rule(tmp_path, monkeypatch):
    (tmp_path / "bad.yaml").write_text(
        "name: bad\nmetrics: [GM]\nrules:\n  - {id: x, type: rise, metric: PPE_TURN, threshold: 1, level: red, question: q}\n",
        encoding="utf-8")
    monkeypatch.setattr(pbmod, "PLAYBOOKS", tmp_path)
    with pytest.raises(ValueError, match="not in this playbook's metrics"):
        pbmod.load("bad")


def test_generic_playbook_has_no_sector_rules():
    d = make_data(["2023-03-31", "2024-03-31", "2025-03-31"])
    r = fin.ratios(fin.line_items(d))
    r["PPE_TURN"] = [2.0, 1.0, 0.5]                                  # would trip asset_turn_drop
    assert not any(q["rule"] == "asset_turn_drop" for q in fin.questions(r, playbook="generic"))
    assert any(q["rule"] == "asset_turn_drop" for q in fin.questions(r, playbook="neocloud"))


def test_evidence_limited_to_playbook_metrics():
    r = fin.ratios(fin.line_items(make_data(["2023-03-31", "2024-03-31"])))
    gen = pbmod.load("generic")
    assert {e["metric"] for e in fin.evidence(r, metrics=gen["metrics"])} <= set(gen["metrics"])


# ---------- 2026-11-25 review ----------
from research import market as mk  # noqa: E402


def test_excess_return_uses_stock_dates_for_halted_stock():
    idx = pd.bdate_range("2025-01-01", "2026-06-30")
    stock = pd.Series(100.0, index=idx)
    stock[idx > "2026-03-31"] = np.nan                                # halted after March
    index = pd.Series(np.where(idx > "2026-03-31", 200.0, 100.0), index=idx)  # index doubles after the halt
    px = pd.DataFrame({"AAA.T": stock, "^N225": index})
    row = mk.performance(px, ["AAA.T"]).loc["AAA.T"]
    assert row["1년 지수 대비(%p)"] == pytest.approx(0.0)


def test_benchmarks_by_exchange():
    assert mk.benchmark_for("1234.KQ")[0] == "^KQ11"
    assert mk.benchmark_for("1234.TWO") is None                      # no TPEx index on Yahoo: none, not TAIEX
    assert mk.benchmark_for("2330.TW")[0] == "^TWII"
    assert mk.benchmark_for("D05.SI")[0] == "^STI"


def _streak_table(values, dates):
    r = pd.DataFrame({m: np.nan for m in fin.METRICS}, index=[fin.fy_label(pd.Timestamp(d)) for d in dates])
    r["CFO_NI"] = values
    return r


def test_streak_ignores_old_years_behind_a_missing_one():
    gen = pbmod.load("generic")
    dates = ["2021-03-31", "2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31"]
    # last year missing (loss year): the old run of weak years must not count as "recent"
    r = _streak_table([0.5, 0.5, 0.5, 0.5, np.nan], dates)
    assert not [q for q in pbmod.questions(r, gen) if q["rule"] == "cash_conversion"]
    r = _streak_table([1.0, 1.0, 0.5, 0.5, 0.5], dates)
    hits = [q for q in pbmod.questions(r, gen) if q["rule"] == "cash_conversion"]
    assert hits and hits[0]["evidence_id"] == "CFO_NI_FY2025"
    gap = _streak_table([0.5, 0.5, 0.5], ["2021-03-31", "2023-03-31", "2024-03-31"])  # FY2022 missing
    assert not [q for q in pbmod.questions(gap, gen) if q["rule"] == "cash_conversion"]


@pytest.mark.parametrize("yaml_text, match", [
    ("name: a\nextends: a\n", "Circular"),
    ("name: a\nmetrics: [GM]\nrules:\n  - {type: rise, metric: GM, threshold: 1, level: red, question: q}\n", "needs an id"),
    ("name: a\nmetrics: [GM]\nrules:\n  - {id: x, type: rise, metric: GM, level: red, question: q}\n", "threshold"),
    ("name: a\nmetrics: [GM]\nrules:\n  - {id: x, type: rise, metric: GM, threshold: 1, level: red, question: '{oops}'}\n", "placeholder"),
    ("name: a\nmetrics: [GM]\nrules:\n  - {id: x, type: streak_below, metric: GM, threshold: 1, years: 1, level: red, question: q}\n", "years"),
])
def test_playbook_load_errors(tmp_path, monkeypatch, yaml_text, match):
    (tmp_path / "a.yaml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(pbmod, "PLAYBOOKS", tmp_path)
    with pytest.raises(ValueError, match=match):
        pbmod.load("a")


def test_red_flags_reach_debate_and_unaddressed_ones_become_unresolved():
    qs = [{"level": "red", "rule": "r1", "evidence_id": "GM_FY2026", "question": "GM 급락?"},
          {"level": "red", "rule": "r2", "evidence_id": "FCF_M_FY2026", "question": "FCF 적자?"},
          {"level": "yellow", "rule": "r3", "evidence_id": "OPM_FY2026", "question": "OPM?"}]
    ev = [{"id": i, "text": i} for i in ("GM_FY2026", "FCF_M_FY2026", "OPM_FY2026")]
    d = Debate("Test", "TEST", ev, dry_run=True, questions=qs)
    assert "GM 급락?" in d._bear_system and "FCF 적자?" in d._judge_system
    d.rounds.append({"bull": "마진은 회복된다 [GM_FY2026]", "bull_bad_ids": [], "bear": {}, "rejected": []})
    d._fake = lambda system: json.dumps({"unresolved": None})
    j = d.judge()
    flagged = [u["question"] for u in j["unresolved"]]
    assert len(flagged) == 1 and "FCF 적자?" in flagged[0]            # yellow and answered red are not added


def test_duplicate_evidence_ids_raise():
    with pytest.raises(ValueError, match="Duplicate"):
        Debate("T", "T", [{"id": "A", "text": "1"}, {"id": "A", "text": "2"}], dry_run=True)


def test_argument_map_marks_rejected_flags_and_verdicts():
    qs = [{"level": "red", "rule": "r", "evidence_id": "PPE_TURN_FY2026", "question": "회전율 하락?"}]
    ev = [{"id": i, "text": f'{i} "값"'} for i in ("GM_FY2026", "FCF_M_FY2026", "PPE_TURN_FY2026")]
    d = Debate("Test", "TEST", ev, dry_run=True, questions=qs)
    d.bull("마진 회복 [GM_FY2026] [NOPE]")
    d.judge()
    m = d.argument_map()
    assert m.startswith("flowchart LR") and "B1 -->|인용| E_GM_FY2026" in m
    assert ":::rejected" in m and ":::flag" in m and ":::supported" in m
    assert "E_NOPE" not in m and '"값"' not in m                       # unknown ids skipped, quotes escaped
    assert "```mermaid" in d.to_markdown()


def test_claude_code_backend(monkeypatch):
    from types import SimpleNamespace
    from research import llm
    monkeypatch.setenv("LLM_BACKEND", "claude-code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-passed")
    monkeypatch.setattr(llm.shutil, "which", lambda _: "/bin/claude")
    reply, seen = {"result": "ok"}, {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.has_credentials() and llm.call_llm("sys", "hello") == "ok"
    assert seen["input"] == "hello" and "ANTHROPIC_API_KEY" not in seen["env"]
    llm.chat("sys", [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}])
    assert seen["input"].index("q1") < seen["input"].index("a1") < seen["input"].index("q2")
    reply = {"result": "usage limit reached", "is_error": True}
    with pytest.raises(RuntimeError, match="usage limit"):
        llm.call_llm("sys", "hello")


# ---------- investment journal ----------
def _trades(rows):
    from research import journal as jr
    t = pd.DataFrame(rows, columns=["date", "ticker", "side", "qty", "price", "fee"])
    t["date"] = pd.to_datetime(t["date"])
    t["currency"] = t["ticker"].map(jr.currency_for)
    return t


def test_journal_rounds_returns_and_weights():
    from research import journal as jr
    t = _trades([
        ["2025-01-02", "AAA.KS", "buy", 10, 100.0, 0.0],
        ["2025-02-03", "AAA.KS", "buy", 10, 200.0, 0.0],    # average cost 150
        ["2025-03-03", "AAA.KS", "sell", 10, 300.0, 0.0],   # half out, 10 left at 150
        ["2025-01-02", "BBB.T", "buy", 1, 1000.0, 0.0],
        ["2025-04-01", "BBB.T", "sell", 1, 1100.0, 0.0],    # round closed: +10%
        ["2025-05-01", "BBB.T", "buy", 2, 1000.0, 0.0],     # new round
    ])
    t = t.sort_values("date", kind="stable")
    rs = jr.rounds(t)
    assert sorted((r["ticker"], r["closed"] is None) for r in rs) == [("AAA.KS", True), ("BBB.T", False), ("BBB.T", True)]
    a = next(r for r in rs if r["ticker"] == "AAA.KS")
    assert a["qty"] == 10 and a["cost_open"] == pytest.approx(1500) and a["buy_cost"] == 3000 and a["proceeds"] == 3000

    idx = pd.to_datetime(["2025-01-02", "2025-06-30"])
    px = pd.DataFrame({"AAA.KS": [100.0, 250.0], "BBB.T": [1000.0, 1500.0], "^KS11": [100.0, 110.0],
                       "^N225": [100.0, 100.0], "JPYKRW=X": [10.0, 10.0]}, index=idx)
    ev = jr.evaluate(rs, px, base="KRW").set_index(["ticker", "status"])
    # AAA: (3000 proceeds + 10 * 250) / 3000 - 1 = +83.3%, index +10% -> +73.3%p
    assert ev.loc[("AAA.KS", "holding"), "return_pct"] == pytest.approx((5500 / 3000 - 1) * 100)
    # index replay: 1000 + 2000 buy 30 units at 100, half sold at 100 (1500), 15 left at 110 -> +5%
    assert ev.loc[("AAA.KS", "holding"), "index_pct"] == pytest.approx(5.0)
    assert ev.loc[("AAA.KS", "holding"), "excess_pp"] == pytest.approx((5500 / 3000 - 1) * 100 - 5)
    closed = ev.loc[("BBB.T", "closed")]
    assert closed["return_pct"] == pytest.approx(10.0)
    # weights in KRW: AAA 2500, BBB 2 * 1500 JPY * 10 = 30000
    assert ev.loc[("AAA.KS", "holding"), "weight_pct"] == pytest.approx(2500 / 32500 * 100)
    assert ev.loc[("BBB.T", "holding"), "weight_pct"] == pytest.approx(30000 / 32500 * 100)
    assert pd.isna(closed["weight_pct"])


def test_journal_rejects_overselling():
    from research import journal as jr
    with pytest.raises(ValueError, match="more shares than held"):
        jr.rounds(_trades([["2025-01-02", "AAA.KS", "buy", 1, 100.0, 0.0], ["2025-01-03", "AAA.KS", "sell", 2, 100.0, 0.0]]))
    with pytest.raises(ValueError, match="no shares"):
        jr.rounds(_trades([["2025-01-03", "AAA.KS", "sell", 1, 100.0, 0.0]]))


def test_journal_kill_criteria_and_public_report_hides_amounts(tmp_path):
    from research import journal as jr
    d = make_data(["2023-12-31", "2024-12-31"], revenue=[100.0, 100.0])
    r = fin.ratios(fin.line_items(d))                      # GM 30%, PPE_TURN 100/180
    (tmp_path / "AAA.KS.yaml").write_text(
        "ticker: AAA.KS\ncompany: Alpha\nstatus: holding\nthesis: 내 논거\n"
        "kill_criteria:\n  - {metric: GM, below: 35}\n  - {metric: PPE_TURN, below: 0.1}\n  - {text: 대주주 변경}\n"
        "reviews:\n  - {date: 2025-03-01, note: 복기}\n", encoding="utf-8")
    theses = jr.load_theses(tmp_path)
    ks = jr.check_kill(theses["AAA.KS"], r)
    assert [k["status"] for k in ks] == ["hit", "ok", "manual"]
    assert ks[0]["evidence"] == "GM_FY2024"
    assert [k["status"] for k in jr.check_kill(theses["AAA.KS"], None)] == ["n/a", "n/a", "manual"]

    t = _trades([["2025-01-02", "AAA.KS", "buy", 37, 12345.0, 77.0], ["2025-01-02", "ZZZ.KS", "buy", 3, 54321.0, 0.0]])
    px = pd.DataFrame({"AAA.KS": [12345.0, 13579.0], "ZZZ.KS": [54321.0, 50000.0], "^KS11": [100.0, 100.0]},
                      index=pd.to_datetime(["2025-01-02", "2025-06-30"]))
    ev = jr.evaluate(jr.rounds(t), px, base="KRW")
    md = jr.public_markdown(ev, theses, {"AAA.KS": ks})
    for secret in ("37", "12345", "12,345", "13579", "13,579", "54321", "54,321", "50,000", "456,765"):
        assert secret not in md
    assert "🔴" in md and "Alpha" in md and "ZZZ.KS" in md and "논거 파일이 없다" in md
    priv = jr.private_markdown(ev)
    assert "13,579.00" in priv and "37.00" in priv          # avg cost includes the fee: 12,347.08


def test_journal_thesis_validation(tmp_path):
    from research import journal as jr
    (tmp_path / "X.yaml").write_text("ticker: X\ncompany: X\nstatus: holding\nthesis: t\nkill_criteria:\n  - {metric: NOPE, below: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kill criterion"):
        jr.load_theses(tmp_path)
    (tmp_path / "X.yaml").write_text("ticker: X\ncompany: X\nstatus: bought\nthesis: t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="status"):
        jr.load_theses(tmp_path)


def test_journal_new_thesis_from_debate(tmp_path):
    from research import journal as jr
    deb = tmp_path / "AAA.KS_20260101_1200.json"
    deb.write_text(json.dumps({"view_after": "토론 후 관점", "judgement": {"unresolved": [{"question": "가동률은?"}]}},
                              ensure_ascii=False), encoding="utf-8")
    p = jr.new_thesis("AAA.KS", "Alpha", debate_json=deb, folder=tmp_path / "theses")
    th = jr.load_theses(tmp_path / "theses")["AAA.KS"]
    assert th["thesis"] == "토론 후 관점" and th["open_questions"] == ["가동률은?"] and th["debate"] == deb.with_suffix(".md").name
    with pytest.raises(FileExistsError):
        jr.new_thesis("AAA.KS", "Alpha", folder=tmp_path / "theses")
    assert p.exists()


def test_journal_example_files_load():
    from research import journal as jr
    trades = jr.load_trades(jr.EXAMPLE_TRADES)
    theses = jr.load_theses(jr.EXAMPLES / "theses")
    assert {r["ticker"] for r in jr.rounds(trades)} <= set(theses)


def test_journal_errors_never_show_amounts(tmp_path):
    from research import journal as jr
    f = tmp_path / "trades.csv"
    f.write_text("date,ticker,side,qty,price,fee\n2025-01-02,A.KS,buy,0,98765,0\n2025-01-03,A.KS,buy,4321,-5,0\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        jr.load_trades(f)
    msg = str(e.value)
    assert "row 2" in msg and "row 3" in msg and "98765" not in msg and "4321" not in msg
    f.write_text("date,ticker,side,qty,price\n2025-01-02,A.KS,buy,4321,98765\n2025-01-03,A.KS,sell,5555,98765\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        jr.rounds(jr.load_trades(f))
    assert "row 3" in str(e.value) and "5555" not in str(e.value) and "4321" not in str(e.value)


def test_journal_input_formats_split_and_empty(tmp_path):
    from research import journal as jr
    f = tmp_path / "trades.csv"
    f.write_text("date,ticker,side,qty,price,fee\n", encoding="utf-8")
    assert jr.rounds(jr.load_trades(f)) == []
    ev = jr.evaluate([], pd.DataFrame())
    assert ev.empty and "(없음)" in jr.public_markdown(ev, {}, {})
    f.write_text('date,ticker,side,qty,price\n2025-01-02,8035.t,buy,10,"1,000"\n2025-02-03,8035.t,split,3,\n', encoding="utf-8")
    t = jr.load_trades(f)
    assert t["ticker"].tolist() == ["8035.T"] * 2 and t["currency"].tolist() == ["JPY"] * 2
    rs = jr.rounds(t)
    assert rs[0]["qty"] == 30 and rs[0]["buy_cost"] == 10000
    px = pd.DataFrame({"8035.T": [1000.0, 370.0], "^N225": [100.0, 100.0], "JPYKRW=X": [9.0, 9.0]},
                      index=pd.to_datetime(["2025-01-02", "2025-06-30"]))
    yahoo = {"8035.T": pd.Series([3.0], index=pd.to_datetime(["2025-02-03"]))}
    ev = jr.evaluate(rs, px, splits=yahoo)
    assert ev["return_pct"].iloc[0] == pytest.approx(11.0) and not ev["split_suspect"].iloc[0]
    # the same holding without the split row: flagged, not silently shown as -63%
    ev = jr.evaluate(jr.rounds(t.iloc[:1]), px, splits=yahoo)
    assert ev["split_suspect"].iloc[0]
    assert "액면분할" in jr.public_markdown(ev, {}, {})
    # no split history (Yahoo unreachable): a price far from the average cost is still flagged
    px.loc[px.index[-1], "8035.T"] = 100.0
    assert jr.evaluate(jr.rounds(t.iloc[:1]), px)["split_suspect"].iloc[0]


def test_journal_missing_fx_or_price_does_not_respread_weights():
    from research import journal as jr
    t = _trades([["2025-01-02", "A.KS", "buy", 1, 100.0, 0.0], ["2025-01-02", "B.T", "buy", 1, 100.0, 0.0],
                 ["2025-01-02", "C.KS", "buy", 1, 100.0, 0.0]])
    px = pd.DataFrame({"A.KS": [100.0, 100.0], "B.T": [100.0, 100.0], "^KS11": [1.0, 1.0], "^N225": [1.0, 1.0]},
                      index=pd.to_datetime(["2025-01-02", "2025-06-30"]))      # no JPYKRW=X, no C.KS column
    ev = jr.evaluate(jr.rounds(t), px)
    assert ev["weight_pct"].isna().all()
    md = jr.public_markdown(ev, {}, {})
    assert "`B.T`: 현재가나 환율이 없어" in md and "`C.KS`: 현재가나 환율이 없어" in md


def test_journal_index_needs_data_from_the_buy_date():
    from research import journal as jr
    idx = pd.Series([100.0, 120.0], index=pd.to_datetime(["2025-03-01", "2025-06-30"]))
    end = pd.Timestamp("2025-06-30")
    assert np.isnan(jr.index_return([(pd.Timestamp("2025-01-02"), "buy", 100.0)], idx, end, 100.0))
    assert jr.index_return([(pd.Timestamp("2025-03-01"), "buy", 100.0)], idx, end, 100.0) == pytest.approx(20.0)


def test_journal_thesis_yaml_edge_cases(tmp_path):
    from research import journal as jr
    ok = "company: X\nstatus: watching\nthesis: t\n"
    (tmp_path / "7203.T.yaml").write_text("ticker: 7203.t\n" + ok + "kill_criteria:\nreviews:\nopen_questions:\n", encoding="utf-8")
    th = jr.load_theses(tmp_path)["7203.T"]
    assert th["kill_criteria"] == [] and th["reviews"] == []
    for bad, match in (("ticker: 7203.T\n" + ok + "kill_criteria:\n  - {metric: GM, below: yes}\n", "kill criterion"),
                       ("ticker: 7203.T\n" + ok + "kill_criteria:\n  - {metric: GM, below: .nan}\n", "kill criterion"),
                       ("ticker: 7203.T\n" + ok + "kill_criteria:\n  - 대주주 변경\n", "must be"),
                       ("ticker: 7203.T\n" + ok + "open_questions: 가동률\n", "must be a list"),
                       ("ticker: 9999.T\n" + ok, "does not match")):
        (tmp_path / "7203.T.yaml").write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            jr.load_theses(tmp_path)


def test_journal_own_theses_win_before_the_first_trade(tmp_path, monkeypatch):
    from research import journal as jr
    monkeypatch.setattr(jr, "THESES", tmp_path / "theses")
    monkeypatch.setattr(jr, "TRADES", tmp_path / "trades.csv")
    assert jr.sources()[2] is True                       # nothing of my own yet: examples
    jr.new_thesis("AAA.KS", "Alpha", folder=tmp_path / "theses")
    trades, theses, is_example = jr.sources()
    assert not is_example and theses == tmp_path / "theses" and not trades.exists()
