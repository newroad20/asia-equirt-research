"""Broker sync, offline: httpx.MockTransport answers in the shapes the brokers document."""
import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research import brokers as br  # noqa: E402
from research import journal as jr  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "TOKENS", tmp_path / "tokens")
    monkeypatch.setattr(br, "SUFFIX_CACHE", tmp_path / "krx.json")
    monkeypatch.setattr(br.time, "sleep", lambda s: None)


def resolve(code):
    return ".KQ" if code == "042700" else ".KS"


def kis_transport(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "tok", "access_token_token_expired": "2099-01-01 00:00:00"})
        p = dict(req.url.params)
        if req.url.path.endswith("inquire-daily-ccld"):
            if p["CTX_AREA_NK100"] == "":  # first page, more to come
                return httpx.Response(200, headers={"tr_cont": "M"}, json={"rt_cd": "0", "ctx_area_fk100": "F", "ctx_area_nk100": "N2", "output1": [
                    {"ord_dt": p["INQR_STRT_DT"], "ord_tmd": "090101", "odno": "1", "pdno": "000660", "sll_buy_dvsn_cd": "02", "tot_ccld_qty": "10", "avg_prvs": "200000"},
                    {"ord_dt": p["INQR_STRT_DT"], "ord_tmd": "090200", "odno": "2", "pdno": "000660", "sll_buy_dvsn_cd": "02", "tot_ccld_qty": "0", "avg_prvs": "0"}]})
            return httpx.Response(200, headers={"tr_cont": "D"}, json={"rt_cd": "0", "output1": [
                {"ord_dt": p["INQR_STRT_DT"], "ord_tmd": "100000", "odno": "3", "pdno": "042700", "sll_buy_dvsn_cd": "01", "tot_ccld_qty": "5", "avg_prvs": "90000"}]})
        if req.url.path.endswith("inquire-ccnl"):
            return httpx.Response(200, headers={"tr_cont": "D"}, json={"rt_cd": "0", "output": [
                {"ord_dt": p["ORD_STRT_DT"], "ord_tmd": "091500", "odno": "9", "pdno": "3778", "ovrs_excg_cd": "TKSE", "sll_buy_dvsn_cd": "02", "ft_ccld_qty": "100", "ft_ccld_unpr3": "3000", "tr_crcy_cd": "JPY"},
                {"ord_dt": p["ORD_STRT_DT"], "ord_tmd": "091600", "odno": "10", "pdno": "00700", "ovrs_excg_cd": "SEHK", "sll_buy_dvsn_cd": "02", "ft_ccld_qty": "100", "ft_ccld_unpr3": "400", "tr_crcy_cd": "HKD"},
                {"ord_dt": p["ORD_STRT_DT"], "ord_tmd": "091700", "odno": "11", "pdno": "VNM", "ovrs_excg_cd": "VNSE", "sll_buy_dvsn_cd": "02", "ft_ccld_qty": "1", "ft_ccld_unpr3": "1", "tr_crcy_cd": "VND"}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_kis_domestic_and_overseas_paging_mapping_and_headers():
    seen = []
    kis = br.KIS("key", "secret", "12345678-01", "real", transport=kis_transport(seen))
    today = date.today()
    d = kis.domestic(today, today)
    assert [(x["code"], x["side"], x["qty"], x["price"]) for x in d] == [("000660", "buy", 10, 200000), ("042700", "sell", 5, 90000)]
    calls = [r for r in seen if r.url.path.endswith("inquire-daily-ccld")]
    assert len(calls) == 2 and calls[0].headers["tr_id"] == "TTTC0081R" and calls[1].headers["tr_cont"] == "N"
    assert dict(calls[1].url.params)["CTX_AREA_NK100"] == "N2"
    o = kis.overseas(today, today)
    assert [(x["ticker"], x["currency"]) for x in o] == [("3778.T", "JPY"), ("0700.HK", "HKD")]   # VNSE skipped
    assert sum(r.url.path == "/oauth2/tokenP" for r in seen) == 1                                # token reused


def test_kis_old_dates_use_the_before_3_months_tr_and_demo_prefix():
    seen = []
    kis = br.KIS("key", "secret", "12345678-01", "demo", transport=kis_transport(seen))
    kis.domestic(date(2024, 1, 1), date(2024, 1, 31))
    assert {r.headers["tr_id"] for r in seen if r.url.path.endswith("ccld")} == {"VTSC9215R"}


def test_only_inquiry_endpoints_can_be_called():
    kis = br.KIS("k", "s", "12345678-01", transport=kis_transport([]))
    with pytest.raises(br.BrokerError, match="blocked"):
        kis._get("/uapi/domestic-stock/v1/trading/order-cash", "TTTC0802U", {}, "a", "b")
    kw = br.Kiwoom("k", "s", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(br.BrokerError, match="blocked"):
        kw._post("/api/dostk/ordr", "kt10000", {}, "x")


def kiwoom_transport(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "tok", "token_type": "bearer", "expires_dt": "20990101000000", "return_code": 0})
        body = json.loads(req.content)
        side_rows = {"4": [{"trde_dt": "20250602", "cntr_dt": "20250602", "proc_tm": "09:01:00", "trde_no": "1", "stk_cd": "A005930",
                            "trde_qty_jwa_cnt": "10", "trde_unit": "60000", "cmsn": "+000000000090", "trde_agri_tax": "0", "incm_resi_tax": "0"}],
                     "5": [{"trde_dt": "20250610", "cntr_dt": "20250610", "proc_tm": "10:00:00", "trde_no": "2", "stk_cd": "A005930",
                            "trde_qty_jwa_cnt": "4", "trde_unit": "65000", "cmsn": "39", "trde_agri_tax": "-468", "incm_resi_tax": "0"}]}
        if req.headers["cont-yn"] == "" and body["tp"] == "4":
            return httpx.Response(200, headers={"cont-yn": "Y", "next-key": "K2"}, json={"return_code": 0, "trst_ovrl_trde_prps_array": side_rows["4"]})
        rows = [] if body["tp"] == "4" else side_rows["5"]
        return httpx.Response(200, headers={"cont-yn": "N"}, json={"return_code": 0, "trst_ovrl_trde_prps_array": rows})
    return httpx.MockTransport(handler)


def test_kiwoom_kt00015_buys_and_sells_with_fees():
    seen = []
    kw = br.Kiwoom("appkey123", "secret", transport=kiwoom_transport(seen))
    rows = kw.domestic(date(2025, 6, 1), date(2025, 6, 30))
    assert [(x["code"], x["side"], x["qty"], x["price"], x["fee"]) for x in rows] == [
        ("A005930", "buy", 10, 60000, 90), ("A005930", "sell", 4, 65000, 507)]
    acnt = [r for r in seen if r.url.path == "/api/dostk/acnt"]
    assert all(r.headers["api-id"] == "kt00015" for r in acnt)
    assert acnt[1].headers["cont-yn"] == "Y" and acnt[1].headers["next-key"] == "K2"


def test_sync_merges_without_duplicates_and_keeps_manual_rows(tmp_path):
    path = tmp_path / "trades.csv"
    path.write_text("date,ticker,side,qty,price,fee\n2024-12-01,2330.TW,buy,1,1000,0\n", encoding="utf-8")
    kw = br.Kiwoom("appkey123", "secret", transport=kiwoom_transport([]))
    r1 = br.sync("2025-06-01", "2025-06-30", path=path, brokers=[kw], resolve=resolve)
    r2 = br.sync("2025-06-01", "2025-06-30", path=path, brokers=[kw], resolve=resolve)     # re-run: no duplicates
    assert r1["total_rows"] == r2["total_rows"] == 3
    assert set(r1) == {"fetched_fills", "trades_written", "total_rows", "file"}               # counts only, no amounts
    t = jr.load_trades(path)
    assert t["ticker"].tolist() == ["2330.TW", "005930.KS", "005930.KS"]
    assert t["side"].tolist() == ["buy", "buy", "sell"] and t["fee"].tolist() == [0, 90, 507]
    assert jr.rounds(t)[1]["qty"] == 6


def test_to_trades_combines_partial_fills_of_one_order():
    rows = [{"date": "20250602", "code": "000660", "side": "buy", "qty": 3, "price": 100.0, "fee": 1.0, "currency": "KRW", "source": "kis", "ref": "kis:1"},
            {"date": "20250602", "code": "000660", "side": "buy", "qty": 1, "price": 104.0, "fee": 0.5, "currency": "KRW", "source": "kis", "ref": "kis:1"}]
    t = br.to_trades(rows, resolve)
    assert len(t) == 1 and t.loc[0, "qty"] == 4 and t.loc[0, "price"] == pytest.approx(101.0) and t.loc[0, "fee"] == 1.5
    assert t.loc[0, "ticker"] == "000660.KS"


def test_errors_and_missing_config(monkeypatch):
    bad = httpx.MockTransport(lambda r: httpx.Response(200, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"})
                              if r.url.path != "/oauth2/tokenP" else httpx.Response(200, json={"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}))
    with pytest.raises(br.BrokerError, match="EGW00123"):
        br.KIS("k", "s", "12345678-01", transport=bad).domestic(date.today(), date.today())
    with pytest.raises(br.BrokerError, match="12345678-01"):
        br.KIS("k", "s", "1234")
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT", "KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(br, "_env", lambda: None)
    with pytest.raises(br.BrokerError, match=".env"):
        br.sync("2025-01-01")
