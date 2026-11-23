"""Annual financial statements -> ratio table -> citable evidence -> 'why?' questions.

Data source: Yahoo Finance via yfinance (works for .T Japan, .KS/.KQ Korea, .TW/.TWO Taiwan,
.HK Hong Kong, .SS/.SZ China). Raw statements are cached under data/cache/<ticker>/ so the
notebooks re-run offline and give the same numbers. Yahoo data is a convenience source:
check important numbers against the company's own filings (EDINET / DART / MOPS / HKEX).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"

# Yahoo row names we use. Lists = first one present wins.
ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "gross_profit": ["Gross Profit"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "net_income": ["Net Income Common Stockholders", "Net Income"],
    "pretax_income": ["Pretax Income"],
    "tax": ["Tax Provision"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "da": ["Depreciation And Amortization", "Reconciled Depreciation"],
    "cfo": ["Operating Cash Flow"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "fcf": ["Free Cash Flow"],
    "dividends": ["Cash Dividends Paid"],
    "stock_issued": ["Issuance Of Capital Stock", "Common Stock Issuance"],
    "total_assets": ["Total Assets"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
    "total_debt": ["Total Debt"],
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "net_ppe": ["Net PPE"],
    "construction_in_progress": ["Construction In Progress"],
    "receivables": ["Accounts Receivable"],
    "invested_capital": ["Invested Capital"],
    "shares": ["Ordinary Shares Number", "Share Issued"],
}


# ---------- fetch ----------
def fetch(ticker: str, refresh: bool = False) -> dict:
    """Return {'income','balance','cashflow': DataFrame (rows=fiscal year), 'info': dict}.

    Uses the cache unless refresh=True or nothing is cached yet.
    """
    folder = CACHE / ticker
    files = {k: folder / f"{k}.csv" for k in ("income", "balance", "cashflow")}
    info_file = folder / "info.json"
    if not refresh and all(p.exists() for p in files.values()) and info_file.exists():
        out = {k: pd.read_csv(p, index_col=0, parse_dates=True) for k, p in files.items()}
        out["info"] = json.loads(info_file.read_text(encoding="utf-8"))
        return out

    import yfinance as yf
    from .net import yf_session

    t = yf.Ticker(ticker, session=yf_session())
    raw = {"income": t.financials, "balance": t.balance_sheet, "cashflow": t.cashflow}
    if raw["income"] is None or raw["income"].empty:
        raise ValueError(f"No financial statements returned for {ticker}. Check the ticker suffix.")
    info = t.info or {}
    keep = ("longName", "shortName", "currency", "financialCurrency", "sector", "industry",
            "country", "marketCap", "lastFiscalYearEnd", "longBusinessSummary")
    info = {k: info.get(k) for k in keep}

    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for k, df in raw.items():
        df = df.T.sort_index()          # rows = fiscal year end, oldest first
        df.index = pd.to_datetime(df.index)
        df.to_csv(files[k])
        out[k] = df
    info_file.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    out["info"] = info
    return out


def _pick(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for n in names:
        if n in df.columns and df[n].notna().any():
            return df[n].astype(float)
    return pd.Series(np.nan, index=df.index)


def line_items(data: dict) -> pd.DataFrame:
    """One tidy table of the raw line items we need (rows = fiscal year)."""
    merged = pd.concat([data["income"], data["balance"], data["cashflow"]], axis=1)
    merged = merged.loc[:, ~merged.columns.duplicated()]
    items = pd.DataFrame({k: _pick(merged, v) for k, v in ROWS.items()})
    items["capex"] = -items["capex"].abs()  # Yahoo reports capex negative; force the sign
    if items["fcf"].isna().all():
        items["fcf"] = items["cfo"] + items["capex"]
    items = items.dropna(subset=["revenue"])
    items.index = [fy_label(d) for d in items.index]
    items.index.name = "fiscal_year"
    return items


def fy_label(d) -> str:
    """FY named by the calendar year it ends in, with the month: FY2026(03) = year ending Mar-2026."""
    d = pd.Timestamp(d)
    return f"FY{d.year}({d.month:02d})"


# ---------- ratios ----------
METRICS = {
    # code: (Korean name, unit, what it tells you)
    "REV": ("매출", "abs", "규모"),
    "REV_G": ("매출 성장률", "%", "수요가 실제로 늘고 있는가"),
    "GM": ("매출총이익률", "%", "가격 결정력, 원가(전력·GPU 감가상각) 부담"),
    "OPM": ("영업이익률", "%", "본업 수익성"),
    "EBITDA_M": ("EBITDA 마진", "%", "감가상각 전 현금 창출력, 설비 산업의 기본 지표"),
    "DA_REV": ("감가상각비/매출", "%", "과거 투자가 비용으로 돌아오는 속도"),
    "CAPEX_REV": ("설비투자/매출", "%", "투자 강도. 네오클라우드는 GPU 구매로 높아짐"),
    "CAPEX_DA": ("설비투자/감가상각비", "x", "1보다 크면 유지보수를 넘는 성장 투자"),
    "FCF_M": ("FCF 마진", "%", "투자 후에 남는 현금"),
    "CFO_NI": ("영업현금흐름/순이익", "x", "이익의 질. 1 미만이 계속되면 의심"),
    "ROE": ("ROE", "%", "자기자본 대비 수익성"),
    "ROIC": ("ROIC(세후 영업이익/투하자본)", "%", "투자한 자본이 돈을 버는가"),
    "PPE_TURN": ("유형자산 회전율(매출/순유형자산)", "x", "GPU·데이터센터 자산이 매출로 얼마나 빨리 바뀌는가"),
    "NET_DEBT_EBITDA": ("순차입금/EBITDA", "x", "빚을 감당할 수 있는가"),
    "DEBT_EQUITY": ("부채비율(총차입금/자기자본)", "x", "레버리지"),
    "INT_COVER": ("이자보상배율(영업이익/이자비용)", "x", "이자를 벌어서 낼 수 있는가"),
    "SHARE_G": ("발행주식수 증가율", "%", "증자로 인한 희석"),
}


def ratios(items: pd.DataFrame) -> pd.DataFrame:
    i = items
    avg = lambda s: (s + s.shift(1)) / 2
    tax_rate = (i["tax"] / i["pretax_income"]).clip(0, 0.5).fillna(0.3)
    net_debt = i["total_debt"] - i["cash"]
    r = pd.DataFrame(index=i.index)
    r["REV"] = i["revenue"]
    r["REV_G"] = i["revenue"].pct_change() * 100
    r["GM"] = i["gross_profit"] / i["revenue"] * 100
    r["OPM"] = i["operating_income"] / i["revenue"] * 100
    r["EBITDA_M"] = i["ebitda"] / i["revenue"] * 100
    r["DA_REV"] = i["da"] / i["revenue"] * 100
    r["CAPEX_REV"] = -i["capex"] / i["revenue"] * 100
    r["CAPEX_DA"] = -i["capex"] / i["da"]
    r["FCF_M"] = i["fcf"] / i["revenue"] * 100
    r["CFO_NI"] = (i["cfo"] / i["net_income"]).where(i["net_income"] > 0)
    r["ROE"] = i["net_income"] / avg(i["equity"]) * 100
    r["ROIC"] = i["operating_income"] * (1 - tax_rate) / avg(i["invested_capital"]) * 100
    r["PPE_TURN"] = i["revenue"] / i["net_ppe"]
    r["NET_DEBT_EBITDA"] = (net_debt / i["ebitda"]).where(i["ebitda"] > 0)
    r["DEBT_EQUITY"] = i["total_debt"] / i["equity"]
    r["INT_COVER"] = (i["operating_income"] / i["interest_expense"].abs()).where(i["interest_expense"].abs() > 0)
    r["SHARE_G"] = i["shares"].pct_change() * 100
    return r.replace([np.inf, -np.inf], np.nan)


# ---------- evidence ----------
def _fmt(v: float, unit: str, currency: str = "") -> str:
    if pd.isna(v):
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "x":
        return f"{v:.2f}x"
    for div, suf in ((1e12, "조"), (1e8, "억")):
        if abs(v) >= div:
            return f"{v / div:,.1f}{suf} {currency}".strip()
    return f"{v:,.0f} {currency}".strip()


def evidence(r: pd.DataFrame, currency: str = "", prefix: str = "") -> list[dict]:
    """Every ratio-year becomes one citable item, id = CODE_FYyyyy (e.g. GM_FY2026)."""
    out = []
    years = list(r.index)
    for code, (name, unit, _why) in METRICS.items():
        for k, fy in enumerate(years):
            v = r.at[fy, code]
            if pd.isna(v):
                continue
            prev = r.at[years[k - 1], code] if k > 0 else np.nan
            text = f"{name} {fy}: {_fmt(v, unit, currency)}"
            if not pd.isna(prev):
                text += f" (전년 {_fmt(prev, unit, currency)})"
            out.append({"id": f"{prefix}{code}_{fy.split('(')[0]}", "metric": code, "period": fy,
                        "value": float(v), "text": text})
    return out


# ---------- questions the numbers raise ----------
def questions(r: pd.DataFrame) -> list[dict]:
    """Rule-based red/yellow flags. Each is a question to answer from the filings, not a conclusion."""
    q = []
    years = list(r.index)

    def add(code, fy, level, text):
        q.append({"level": level, "evidence_id": f"{code}_{fy.split('(')[0]}", "question": text})

    for k in range(1, len(years)):
        fy, pf = years[k], years[k - 1]
        cur, prev = r.loc[fy], r.loc[pf]
        if abs(cur.GM - prev.GM) >= 3:
            add("GM", fy, "yellow", f"{fy} 매출총이익률이 {prev.GM:.1f}%→{cur.GM:.1f}%로 {cur.GM - prev.GM:+.1f}%p 변했다. "
                "가격(단가) 때문인가, 원가(전력·감가상각·외주) 때문인가, 매출 구성 변화 때문인가?")
        if abs(cur.OPM - prev.OPM) >= 3:
            add("OPM", fy, "yellow", f"{fy} 영업이익률 {prev.OPM:.1f}%→{cur.OPM:.1f}%. 매출총이익 변화와 판관비 중 어느 쪽이 컸나?")
        if cur.DA_REV - prev.DA_REV >= 3:
            add("DA_REV", fy, "yellow", f"{fy} 감가상각비/매출이 {prev.DA_REV:.1f}%→{cur.DA_REV:.1f}%로 올랐다. "
                "새로 들인 설비(GPU)가 매출을 따라오기 전에 비용부터 반영되는 중인가? 내용연수 가정은 몇 년인가?")
        if prev.PPE_TURN > 0 and cur.PPE_TURN / prev.PPE_TURN - 1 <= -0.15:
            add("PPE_TURN", fy, "red", f"{fy} 유형자산 회전율 {prev.PPE_TURN:.2f}x→{cur.PPE_TURN:.2f}x. "
                "자산이 매출보다 훨씬 빨리 늘었다. 가동률은? 계약된 수요가 있는 투자인가?")
        if not pd.isna(cur.SHARE_G) and cur.SHARE_G >= 3:
            add("SHARE_G", fy, "yellow", f"{fy} 발행주식수 {cur.SHARE_G:+.1f}%. 증자 자금은 어디에 썼고, 희석을 넘는 이익 증가가 있었나?")
        if cur.REV_G <= 0:
            add("REV_G", fy, "red", f"{fy} 매출 {cur.REV_G:+.1f}%. 일시적 요인인가, 수요 둔화인가?")

    last = years[-1]
    cur = r.loc[last]
    if cur.CAPEX_REV >= 30:
        add("CAPEX_REV", last, "yellow", f"{last} 설비투자가 매출의 {cur.CAPEX_REV:.0f}%다. 투자 회수 기간과 자금 조달(차입·증자·보조금)은?")
    if cur.FCF_M < 0:
        add("FCF_M", last, "red", f"{last} FCF 마진 {cur.FCF_M:.1f}%. 마이너스 FCF는 언제 끝나고, 그때까지 자금은 충분한가?")
    if not pd.isna(cur.NET_DEBT_EBITDA) and cur.NET_DEBT_EBITDA >= 3:
        add("NET_DEBT_EBITDA", last, "red", f"{last} 순차입금/EBITDA {cur.NET_DEBT_EBITDA:.1f}x. 금리·만기 구조와 약정(covenant)은?")
    recent = r["CFO_NI"].dropna().tail(3)
    if len(recent) >= 2 and (recent < 0.8).all():
        add("CFO_NI", recent.index[-1], "red", "최근 영업현금흐름이 순이익에 계속 못 미친다. 매출채권·선급금이 쌓이고 있나?")
    return q


def peer_table(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    """Latest-year key ratios for several companies side by side."""
    rows = {}
    for t in tickers:
        try:
            d = fetch(t, refresh=refresh)
        except Exception as e:  # one bad ticker should not stop the comparison
            print(f"skip {t}: {e}")
            continue
        r = ratios(line_items(d))
        last = r.iloc[-1]
        name = d["info"].get("shortName") or t
        rows[f"{name} ({t})"] = {"최근 회계연도": r.index[-1], **{METRICS[c][0]: last[c] for c in METRICS if c != "REV"}}
    return pd.DataFrame(rows).T
