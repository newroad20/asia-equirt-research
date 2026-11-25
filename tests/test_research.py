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
