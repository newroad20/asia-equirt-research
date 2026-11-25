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
    "inventory": ["Inventory"],
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
    items["fcf"] = items["fcf"].fillna(items["cfo"] + items["capex"])  # fill only the missing years
    # Keep years that have only balance-sheet data: they are needed for averages (ROE, ROIC).
    # ratios() drops years without revenue at the end.
    items = items.dropna(how="all")
    if items["revenue"].isna().all():
        raise ValueError("No revenue in the returned statements; cannot compute ratios.")
    items.index = [fy_label(d) for d in items.index]
    items.index.name = "fiscal_year"
    return items


def fy_label(d) -> str:
    """FY named by the calendar year it ends in, with the month: FY2026(03) = year ending Mar-2026."""
    d = pd.Timestamp(d)
    return f"FY{d.year}({d.month:02d})"


def _months(label: str) -> int:
    """'FY2026(03)' -> months since year 0, to check whether two periods are consecutive."""
    year, month = label[2:6], label[7:9]
    return int(year) * 12 + int(month)


def _consecutive(index) -> pd.Series:
    """True where the row is about 12 months after the previous row (a fiscal-year-end change
    gives 9-15 months, which still counts). False for the first row and across missing years."""
    m = pd.Series([_months(x) for x in index], index=index)
    gap = m.diff()
    return (gap >= 9) & (gap <= 15)


def period_keys(index) -> dict:
    """Short period key used in evidence ids: 'FY2026(03)' -> 'FY2026'.

    If two periods would share a key (a company changed its fiscal year-end), those keep the
    month ('FY2024M03', 'FY2024M12') so ids never collide.
    """
    short = {fy: fy.split("(")[0] for fy in index}
    counts = pd.Series(list(short.values())).value_counts()
    return {fy: s if counts[s] == 1 else f"{s}M{fy[7:9]}" for fy, s in short.items()}


# ---------- ratios ----------
METRICS = {
    # code: (Korean name, unit, what it tells you)
    "REV": ("매출", "abs", "규모"),
    "REV_G": ("매출 성장률", "%", "수요가 실제로 늘고 있는가"),
    "GM": ("매출총이익률", "%", "가격 결정력과 원가 부담"),
    "OPM": ("영업이익률", "%", "본업 수익성"),
    "EBITDA_M": ("EBITDA 마진", "%", "감가상각 전 현금 창출력, 설비 산업의 기본 지표"),
    "DA_REV": ("감가상각비/매출", "%", "과거 투자가 비용으로 돌아오는 속도"),
    "CAPEX_REV": ("설비투자/매출", "%", "투자 강도"),
    "CAPEX_DA": ("설비투자/감가상각비", "x", "1보다 크면 유지보수를 넘는 성장 투자"),
    "FCF_M": ("FCF 마진", "%", "투자 후에 남는 현금"),
    "CFO_NI": ("영업현금흐름/순이익", "x", "이익의 질. 1 미만이 계속되면 의심"),
    "ROE": ("ROE", "%", "자기자본 대비 수익성"),
    "ROIC": ("ROIC(세후 영업이익/투하자본)", "%", "투자한 자본이 돈을 버는가"),
    "PPE_TURN": ("유형자산 회전율(매출/순유형자산)", "x", "설비 자산이 매출로 얼마나 빨리 바뀌는가"),
    "INV_DAYS": ("재고일수(기말 재고/매출원가)", "일", "팔리지 않은 물건이 쌓이는가"),
    "NET_DEBT_EBITDA": ("순차입금/EBITDA", "x", "빚을 감당할 수 있는가"),
    "DEBT_EQUITY": ("부채비율(총차입금/자기자본)", "x", "레버리지"),
    "INT_COVER": ("이자보상배율(영업이익/이자비용)", "x", "이자를 벌어서 낼 수 있는가"),
    "SHARE_G": ("발행주식수 증가율", "%", "증자로 인한 희석"),
}


def ratios(items: pd.DataFrame) -> pd.DataFrame:
    i = items
    ok = _consecutive(i.index)  # averages and growth only across adjacent fiscal years
    avg = lambda s: ((s + s.shift(1)) / 2).where(ok)
    growth = lambda s: (s / s.shift(1) - 1).where(ok) * 100
    tax_rate = (i["tax"] / i["pretax_income"]).clip(0, 0.5).fillna(0.3)
    net_debt = i["total_debt"] - i["cash"]
    r = pd.DataFrame(index=i.index)
    r["REV"] = i["revenue"]
    r["REV_G"] = growth(i["revenue"])
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
    cogs = i["revenue"] - i["gross_profit"]
    r["INV_DAYS"] = (i["inventory"] / cogs * 365).where(cogs > 0)
    r["NET_DEBT_EBITDA"] = (net_debt / i["ebitda"]).where(i["ebitda"] > 0)
    r["DEBT_EQUITY"] = i["total_debt"] / i["equity"]
    r["INT_COVER"] = (i["operating_income"] / i["interest_expense"].abs()).where(i["interest_expense"].abs() > 0)
    r["SHARE_G"] = growth(i["shares"])
    r = r.replace([np.inf, -np.inf], np.nan)
    return r[i["revenue"].notna()]  # balance-sheet-only years were kept just for the averages


# ---------- evidence ----------
def _fmt(v: float, unit: str, currency: str = "") -> str:
    if pd.isna(v):
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "x":
        return f"{v:.2f}x"
    if unit == "일":
        return f"{v:.0f}일"
    for div, suf in ((1e12, "조"), (1e8, "억")):
        if abs(v) >= div:
            return f"{v / div:,.1f}{suf} {currency}".strip()
    return f"{v:,.0f} {currency}".strip()


def evidence(r: pd.DataFrame, currency: str = "", prefix: str = "", metrics: list[str] | None = None) -> list[dict]:
    """Every ratio-year becomes one citable item, id = CODE_FYyyyy (e.g. GM_FY2026).

    `metrics`: only these metric codes (a playbook's list); default all.
    """
    out = []
    years = list(r.index)
    keys = period_keys(years)
    ok = _consecutive(years)
    for code, (name, unit, _why) in METRICS.items():
        if metrics is not None and code not in metrics:
            continue
        for k, fy in enumerate(years):
            v = r.at[fy, code]
            if pd.isna(v):
                continue
            prev = r.at[years[k - 1], code] if k > 0 and ok.iloc[k] else np.nan
            text = f"{name} {fy}: {_fmt(v, unit, currency)}"
            if not pd.isna(prev):
                text += f" (전년 {_fmt(prev, unit, currency)})"
            out.append({"id": f"{prefix}{code}_{keys[fy]}", "metric": code, "period": fy,
                        "value": float(v), "text": text})
    ids = [e["id"] for e in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate evidence ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    return out


# ---------- questions the numbers raise ----------
def questions(r: pd.DataFrame, prefix: str = "", playbook: str | dict = "generic") -> list[dict]:
    """Questions the numbers raise, from a sector playbook's rules (playbooks/<name>.yaml).

    Pass the same `prefix` you gave evidence() so the question ids match the evidence ids.
    """
    from . import playbook as pbmod  # imported here: playbook.py imports this module

    pb = pbmod.load(playbook) if isinstance(playbook, str) else playbook
    return pbmod.questions(r, pb, prefix=prefix)


def peer_table(tickers: list[str], refresh: bool = False, metrics: list[str] | None = None) -> pd.DataFrame:
    """Latest-year key ratios for several companies side by side (`metrics`: a playbook's list)."""
    metrics = [c for c in (metrics or METRICS) if c != "REV"]
    rows = {}
    for t in tickers:
        try:
            d = fetch(t, refresh=refresh)
            r = ratios(line_items(d))
            last = r.iloc[-1]
        except Exception as e:  # one bad ticker should not stop the comparison
            print(f"skip {t}: {e}")
            continue
        name = d["info"].get("shortName") or t
        rows[f"{name} ({t})"] = {"최근 회계연도": r.index[-1], **{METRICS[c][0]: last[c] for c in metrics}}
    return pd.DataFrame(rows).T
