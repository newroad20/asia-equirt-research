"""Pull executed trades from broker APIs into journal/trades.csv. Read-only by construction.

Supported (checked against the brokers' own documentation, 2026-11):
  한국투자증권 KIS Developers  국내 체결  /uapi/domestic-stock/v1/trading/inquire-daily-ccld
                                         TTTC0081R (3개월 이내), CTSC9215R (3개월 이전)
                               해외 체결  /uapi/overseas-stock/v1/trading/inquire-ccnl  TTTS3035R
                                         (일본·홍콩·중국·미국. 대만은 KIS 해외주식에 없음)
  키움증권 REST API            국내 거래  /api/dostk/acnt  kt00015 위탁종합거래내역 (매수/매도, 수수료·세금 포함)
                                         (키움 REST의 해외주식은 미국뿐이라 여기서는 국내만)

Safety:
  - Only the inquiry endpoints above can be called: every request is checked against an allowlist
    of (path, tr_id / api-id) before it is sent. Order endpoints are not reachable from this module.
  - Keys come from .env (gitignored). Access tokens are cached under data/ (gitignored).
  - Nothing here prints quantities, prices or amounts; sync() reports counts only.

.env keys:
  KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT=12345678-01, KIS_ENV=real|demo
  KIWOOM_APP_KEY, KIWOOM_SECRET_KEY, KIWOOM_ENV=real|demo
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "data" / "broker_tokens"
SUFFIX_CACHE = ROOT / "data" / "cache" / "krx_suffix.json"

# ---------- allowlists: the only calls this module can make ----------
KIS_HOSTS = {"real": "https://openapi.koreainvestment.com:9443", "demo": "https://openapivts.koreainvestment.com:29443"}
KIS_ALLOWED = {
    "/uapi/domestic-stock/v1/trading/inquire-daily-ccld": {"TTTC0081R", "CTSC9215R", "VTTC0081R", "VTSC9215R"},
    "/uapi/overseas-stock/v1/trading/inquire-ccnl": {"TTTS3035R", "VTTS3035R"},
}
KIWOOM_HOSTS = {"real": "https://api.kiwoom.com", "demo": "https://mockapi.kiwoom.com"}
KIWOOM_ALLOWED = {"/api/dostk/acnt": {"kt00015"}}

# KIS overseas exchange code -> Yahoo suffix
KIS_EXCHANGE_SUFFIX = {"TKSE": ".T", "SEHK": ".HK", "SHAA": ".SS", "SZAA": ".SZ", "NASD": "", "NYSE": "", "AMEX": ""}
KIS_EXCHANGE_CURRENCY = {"TKSE": "JPY", "SEHK": "HKD", "SHAA": "CNY", "SZAA": "CNY", "NASD": "USD", "NYSE": "USD", "AMEX": "USD"}


class BrokerError(RuntimeError):
    pass


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass


def _client(transport=None):
    import httpx
    from .net import ca_bundle

    if transport is not None:  # tests
        return httpx.Client(transport=transport, timeout=30)
    return httpx.Client(verify=ca_bundle(), timeout=30)


def _num(v) -> float:
    """'+000000001234' / '-12.5' / '' -> float."""
    s = str(v or "").replace(",", "").strip()
    return float(s) if s not in ("", "+", "-") else 0.0


def _months(start: date, end: date):
    """[start, end] cut into calendar-month chunks (APIs cap the range; month chunks stay well inside)."""
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(end, nxt - timedelta(days=1))
        cur = nxt


def _cached_token(name: str):
    f = TOKENS / f"{name}.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        if datetime.fromisoformat(d["expires"]) > datetime.now() + timedelta(minutes=10):
            return d["token"]
    return None


def _save_token(name: str, token: str, expires: datetime):
    TOKENS.mkdir(parents=True, exist_ok=True)
    (TOKENS / f"{name}.json").write_text(json.dumps({"token": token, "expires": expires.isoformat()}), encoding="utf-8")


# ---------- KRX suffix (.KS / .KQ) ----------
def krx_ticker(code: str, resolve=None) -> str:
    """6-digit KRX code -> Yahoo ticker. KOSPI (.KS) or KOSDAQ (.KQ) is decided once from Yahoo
    (whichever has prices) and cached in data/cache/krx_suffix.json."""
    code = code.strip()
    code = code[1:] if len(code) == 7 else code  # Kiwoom prefixes A (stock) / J (ELW) / Q (ETN)
    cache = json.loads(SUFFIX_CACHE.read_text(encoding="utf-8")) if SUFFIX_CACHE.exists() else {}
    if code not in cache:
        cache[code] = (resolve or _resolve_krx)(code)
        SUFFIX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SUFFIX_CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return code + cache[code]


def _resolve_krx(code: str) -> str:
    import yfinance as yf
    from .net import yf_session

    s = yf_session()
    for suffix in (".KS", ".KQ"):
        try:
            if not yf.Ticker(code + suffix, session=s).history(period="5d").empty:
                return suffix
        except Exception:
            pass
    return ".KS"


# ---------- 한국투자증권 ----------
class KIS:
    def __init__(self, app_key: str, app_secret: str, account: str, env: str = "real", transport=None):
        if env not in KIS_HOSTS:
            raise BrokerError("KIS_ENV must be real or demo")
        cano, _, prod = account.partition("-")
        if len(cano) != 8 or len(prod) != 2:
            raise BrokerError("KIS_ACCOUNT must look like 12345678-01")
        self.key, self.secret, self.cano, self.prod, self.env = app_key, app_secret, cano, prod, env
        self.host = KIS_HOSTS[env]
        self.http = _client(transport)
        self._token = None

    @classmethod
    def from_env(cls):
        _env()
        k, s, a = (os.environ.get(x) for x in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT"))
        return cls(k, s, a, os.environ.get("KIS_ENV", "real")) if k and s and a else None

    def token(self) -> str:
        # KIS limits how often a token can be issued; a token lasts about a day, so reuse it
        name = f"kis_{self.env}_{self.cano}"
        self._token = self._token or _cached_token(name)
        if not self._token:
            r = self.http.post(f"{self.host}/oauth2/tokenP",
                               json={"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
            d = r.json() if r.content else {}
            if r.status_code != 200 or "access_token" not in d:
                raise BrokerError(f"KIS token failed: {d.get('error_description') or r.status_code}")
            self._token = d["access_token"]
            _save_token(name, self._token, datetime.strptime(d["access_token_token_expired"], "%Y-%m-%d %H:%M:%S"))
        return self._token

    def _get(self, path: str, tr_id: str, params: dict, fk: str, nk: str) -> list[dict]:
        if tr_id not in KIS_ALLOWED.get(path, set()):
            raise BrokerError(f"blocked: {path} {tr_id} is not an allowed inquiry")
        if self.env == "demo" and tr_id[0] in "TJC":
            tr_id = "V" + tr_id[1:]
        rows, cont, keys = [], "", {fk: "", nk: ""}
        for _ in range(200):  # page limit: a runaway loop must end
            headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token()}",
                       "appkey": self.key, "appsecret": self.secret, "tr_id": tr_id, "custtype": "P", "tr_cont": cont}
            r = self.http.get(f"{self.host}{path}", headers=headers, params={**params, **keys})
            d = r.json() if r.content else {}
            if r.status_code != 200 or d.get("rt_cd") != "0":
                raise BrokerError(f"KIS {tr_id}: {d.get('msg_cd', r.status_code)} {d.get('msg1', '')}".strip())
            rows += d.get("output1") or d.get("output") or []
            if r.headers.get("tr_cont") not in ("M", "F"):
                return rows
            cont, keys = "N", {fk: d.get(fk.lower(), ""), nk: d.get(nk.lower(), "")}
            time.sleep(0.5 if self.env == "demo" else 0.1)
        raise BrokerError(f"KIS {tr_id}: too many pages")

    def domestic(self, start: date, end: date) -> list[dict]:
        boundary = date.today() - timedelta(days=89)  # TTTC0081R: last 3 months, CTSC9215R: older
        out = []
        for s, e in _months(start, end):
            for a, b, tr in ((s, min(e, boundary - timedelta(days=1)), "CTSC9215R"), (max(s, boundary), e, "TTTC0081R")):
                if a > b:
                    continue
                rows = self._get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", tr, {
                    "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "INQR_STRT_DT": f"{a:%Y%m%d}", "INQR_END_DT": f"{b:%Y%m%d}",
                    "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "01", "PDNO": "", "CCLD_DVSN": "01", "ORD_GNO_BRNO": "", "ODNO": "",
                    "INQR_DVSN_3": "00", "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "ALL"}, "CTX_AREA_FK100", "CTX_AREA_NK100")
                for x in rows:
                    qty = _num(x.get("tot_ccld_qty"))
                    if qty <= 0 or x.get("sll_buy_dvsn_cd") not in ("01", "02"):
                        continue
                    out.append({"date": x["ord_dt"], "code": x["pdno"], "side": "sell" if x["sll_buy_dvsn_cd"] == "01" else "buy",
                                "qty": qty, "price": _num(x.get("avg_prvs")), "fee": 0.0, "currency": "KRW",
                                "source": "kis", "ref": f"kis:{x['ord_dt']}:{x.get('ord_tmd', '')}:{x.get('odno', '')}"})
        return out

    def overseas(self, start: date, end: date) -> list[dict]:
        out, skipped = [], set()
        for s, e in _months(start, end):
            demo = self.env == "demo"
            rows = self._get("/uapi/overseas-stock/v1/trading/inquire-ccnl", "TTTS3035R", {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prod, "PDNO": "" if demo else "%",
                "ORD_STRT_DT": f"{s:%Y%m%d}", "ORD_END_DT": f"{e:%Y%m%d}", "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "01" if not demo else "00",
                "OVRS_EXCG_CD": "" if demo else "%", "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": ""},
                "CTX_AREA_FK200", "CTX_AREA_NK200")
            for x in rows:
                qty, exch = _num(x.get("ft_ccld_qty")), x.get("ovrs_excg_cd", "")
                if qty <= 0 or x.get("sll_buy_dvsn_cd") not in ("01", "02"):
                    continue
                if exch not in KIS_EXCHANGE_SUFFIX:
                    skipped.add(exch)
                    continue
                code = x["pdno"].strip()
                if exch == "SEHK":
                    code = code.lstrip("0").zfill(4)  # Yahoo: 0700.HK
                out.append({"date": x["ord_dt"], "ticker": code + KIS_EXCHANGE_SUFFIX[exch],
                            "side": "sell" if x["sll_buy_dvsn_cd"] == "01" else "buy", "qty": qty,
                            "price": _num(x.get("ft_ccld_unpr3")), "fee": 0.0,
                            "currency": x.get("tr_crcy_cd") or KIS_EXCHANGE_CURRENCY[exch],
                            "source": "kis", "ref": f"kis:{x['ord_dt']}:{x.get('ord_tmd', '')}:{x.get('odno', '')}"})
        if skipped:
            print("KIS 해외: 지원하지 않는 거래소 코드라 건너뜀:", sorted(skipped))
        return out


# ---------- 키움증권 ----------
class Kiwoom:
    def __init__(self, app_key: str, secret_key: str, env: str = "real", transport=None):
        if env not in KIWOOM_HOSTS:
            raise BrokerError("KIWOOM_ENV must be real or demo")
        self.key, self.secret, self.env = app_key, secret_key, env
        self.host = KIWOOM_HOSTS[env]
        self.http = _client(transport)
        self._token = None

    @classmethod
    def from_env(cls):
        _env()
        k, s = os.environ.get("KIWOOM_APP_KEY"), os.environ.get("KIWOOM_SECRET_KEY")
        return cls(k, s, os.environ.get("KIWOOM_ENV", "real")) if k and s else None

    def token(self) -> str:
        name = f"kiwoom_{self.env}_{self.key[:6]}"
        self._token = self._token or _cached_token(name)
        if not self._token:
            r = self.http.post(f"{self.host}/oauth2/token", headers={"content-type": "application/json;charset=UTF-8"},
                               json={"grant_type": "client_credentials", "appkey": self.key, "secretkey": self.secret})
            d = r.json() if r.content else {}
            if r.status_code != 200 or not d.get("token"):
                raise BrokerError(f"Kiwoom token failed: {d.get('return_msg') or r.status_code}")
            self._token = d["token"]
            _save_token(name, self._token, datetime.strptime(d["expires_dt"], "%Y%m%d%H%M%S"))
        return self._token

    def _post(self, path: str, api_id: str, body: dict, key: str) -> list[dict]:
        if api_id not in KIWOOM_ALLOWED.get(path, set()):
            raise BrokerError(f"blocked: {path} {api_id} is not an allowed inquiry")
        rows, cont, nxt = [], "", ""
        for _ in range(200):
            headers = {"content-type": "application/json;charset=UTF-8", "authorization": f"Bearer {self.token()}",
                       "api-id": api_id, "cont-yn": cont, "next-key": nxt}
            r = self.http.post(f"{self.host}{path}", headers=headers, json=body)
            d = r.json() if r.content else {}
            if r.status_code != 200 or str(d.get("return_code", "0")) != "0":
                raise BrokerError(f"Kiwoom {api_id}: {d.get('return_msg') or r.status_code}")
            rows += d.get(key) or []
            if r.headers.get("cont-yn") != "Y":
                return rows
            cont, nxt = "Y", r.headers.get("next-key", "")
            time.sleep(1.1)  # kt00015: 1 call per second
        raise BrokerError(f"Kiwoom {api_id}: too many pages")

    def domestic(self, start: date, end: date) -> list[dict]:
        out = []
        for s, e in _months(start, end):
            for tp, side in (("4", "buy"), ("5", "sell")):  # kt00015 tp: 4 매수, 5 매도
                rows = self._post("/api/dostk/acnt", "kt00015", {
                    "strt_dt": f"{s:%Y%m%d}", "end_dt": f"{e:%Y%m%d}", "tp": tp, "stk_cd": "", "crnc_cd": "",
                    "gds_tp": "1", "frgn_stex_code": "", "dmst_stex_tp": "%", "qry_sort_tp": "2"},
                    "trst_ovrl_trde_prps_array")
                for x in rows:
                    qty = _num(x.get("trde_qty_jwa_cnt"))
                    if qty <= 0 or not x.get("stk_cd"):
                        continue
                    fee = sum(abs(_num(x.get(k))) for k in ("cmsn", "trde_agri_tax", "incm_resi_tax"))
                    out.append({"date": x.get("cntr_dt") or x["trde_dt"], "code": x["stk_cd"], "side": side, "qty": qty,
                                "price": _num(x.get("trde_unit")), "fee": fee, "currency": "KRW",
                                "source": "kiwoom",
                                "ref": f"kiwoom:{x['trde_dt']}:{str(x.get('proc_tm', '')).replace(':', '')}:{x.get('trde_no', '')}"})
                time.sleep(1.1)
        return out


# ---------- sync ----------
COLUMNS = ["date", "ticker", "side", "qty", "price", "fee", "currency", "source", "ref"]


def to_trades(rows: list[dict], resolve=None) -> pd.DataFrame:
    """Broker rows -> trades.csv rows. Same order filled in several pieces (same ref) becomes one row
    at the quantity-weighted price."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    if "ticker" not in df:
        df["ticker"] = None
    if "code" in df:
        krx = df["ticker"].isna()
        df.loc[krx, "ticker"] = df.loc[krx, "code"].map(lambda c: krx_ticker(c, resolve))
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["amount"] = df["qty"] * df["price"]
    g = df.groupby(["source", "ref", "date", "ticker", "side", "currency"], as_index=False, sort=False)[["qty", "amount", "fee"]].sum()
    g["price"] = (g["amount"] / g["qty"]).round(6)
    return g[COLUMNS].sort_values(["date", "ref"], kind="stable").reset_index(drop=True)


def merge(existing: pd.DataFrame, fetched: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Rows typed by hand (no source) are kept. Broker rows inside [start, end] are replaced by the
    fresh fetch for that broker, so re-running a sync never duplicates trades."""
    if existing.empty:
        return fetched.reset_index(drop=True)
    ex = existing.copy()
    for c in COLUMNS:
        if c not in ex:
            ex[c] = ""
    ex["source"] = ex["source"].fillna("")
    d = pd.to_datetime(ex["date"])
    synced = set(fetched["source"]) if not fetched.empty else set()
    drop = ex["source"].isin(synced) & (d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))
    out = pd.concat([ex[~drop], fetched], ignore_index=True)
    out["_d"] = pd.to_datetime(out["date"])
    return out.sort_values("_d", kind="stable").drop(columns="_d").reset_index(drop=True)


def sync(start: date | str, end: date | str | None = None, path: Path | None = None,
         brokers: list | None = None, resolve=None) -> dict:
    """Fetch executed trades from every configured broker and merge them into journal/trades.csv.
    Returns counts per source only (never amounts)."""
    from .journal import TRADES

    path = path or TRADES
    start = pd.Timestamp(start).date()
    end = pd.Timestamp(end).date() if end else date.today()
    if brokers is None:
        brokers = [b for b in (KIS.from_env(), Kiwoom.from_env()) if b]
        if not brokers:
            raise BrokerError(".env에 KIS_APP_KEY/KIS_APP_SECRET/KIS_ACCOUNT 또는 KIWOOM_APP_KEY/KIWOOM_SECRET_KEY가 없습니다.")
    rows, counts = [], {}
    for b in brokers:
        got = b.domestic(start, end) + (b.overseas(start, end) if hasattr(b, "overseas") else [])
        counts[type(b).__name__] = len(got)
        rows += got
    fetched = to_trades(rows, resolve)
    existing = pd.read_csv(path, dtype=str, comment="#", keep_default_na=False) if path.exists() else pd.DataFrame()
    merged = merge(existing, fetched, start, end)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(path, index=False)
    return {"fetched_fills": counts, "trades_written": len(fetched), "total_rows": len(merged), "file": str(path)}


if __name__ == "__main__":  # python -m research.brokers 2024-01-01 [2026-11-26]
    import argparse

    ap = argparse.ArgumentParser(description="증권사 체결 내역을 journal/trades.csv로 가져오기 (조회 전용)")
    ap.add_argument("start", help="시작일 YYYY-MM-DD")
    ap.add_argument("end", nargs="?", help="종료일 (기본: 오늘)")
    a = ap.parse_args()
    print(sync(a.start, a.end))
