"""Market overview: price performance vs the home index, valuation snapshot, recent headlines.

Source: Yahoo Finance (yfinance). Results are cached per day under data/cache/market/<date>/ so a
notebook re-run on the same day shows the same numbers. Valuation fields on Yahoo can be stale or
computed on a different basis than the company's filings; treat them as a screen, not as inputs.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

BENCHMARKS = {  # ticker suffix -> (index ticker, name)
    ".T": ("^N225", "Nikkei 225"),
    ".KS": ("^KS11", "KOSPI"), ".KQ": ("^KQ11", "KOSDAQ"),
    ".TW": ("^TWII", "TAIEX"),
    # .TWO (Taipei Exchange / TPEx): Yahoo has no TPEx index series, so no benchmark rather than a wrong one
    ".HK": ("^HSI", "Hang Seng"),
    ".SS": ("000300.SS", "CSI 300"), ".SZ": ("000300.SS", "CSI 300"),
    ".SI": ("^STI", "Straits Times"), ".KL": ("^KLSE", "FTSE Bursa Malaysia KLCI"),
    ".BK": ("^SET.BK", "SET"), ".JK": ("^JKSE", "Jakarta Composite"),
    ".NS": ("^NSEI", "Nifty 50"), ".BO": ("^NSEI", "Nifty 50"),
}
VALUATION_FIELDS = {
    "currency": "통화", "marketCap": "시가총액", "trailingPE": "PER(지난 12개월)", "forwardPE": "PER(예상)",
    "priceToBook": "PBR", "enterpriseToEbitda": "EV/EBITDA", "enterpriseToRevenue": "EV/매출",
    "dividendYield": "배당수익률(%)",
}


def benchmark_for(ticker: str) -> tuple[str, str] | None:
    for suffix, bm in sorted(BENCHMARKS.items(), key=lambda kv: -len(kv[0])):
        if ticker.endswith(suffix):
            return bm
    return None


def _cache_dir(asof: date | None) -> Path:
    d = ROOT / "data" / "cache" / "market" / str(asof or date.today())
    d.mkdir(parents=True, exist_ok=True)
    return d


def prices(tickers: list[str], period: str = "2y", refresh: bool = False) -> pd.DataFrame:
    """Adjusted daily closes, one column per ticker, plus each ticker's benchmark index."""
    bms = {b[0] for t in tickers if (b := benchmark_for(t))}
    wanted = list(dict.fromkeys(list(tickers) + sorted(bms)))
    f = _cache_dir(None) / f"prices_{period}.csv"
    if f.exists() and not refresh:
        cached = pd.read_csv(f, index_col=0, parse_dates=True)
        if set(wanted) <= set(cached.columns):
            return cached[wanted]
    import yfinance as yf
    from .net import yf_session

    # threads=False: parallel downloads can lock yfinance's local cache database
    px = yf.download(wanted, period=period, session=yf_session(), progress=False,
                     auto_adjust=True, threads=False)["Close"]
    px = px.reindex(columns=wanted)
    missing = [t for t in wanted if px[t].dropna().empty]
    if missing:
        print("가격을 받지 못한 종목:", missing)
    px.to_csv(f)
    return px


def _ret(s: pd.Series, start) -> float:
    s = s.dropna()
    base = s[s.index <= start]
    if s.empty or base.empty:
        return np.nan
    return (s.iloc[-1] / base.iloc[-1] - 1) * 100


def performance(px: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Returns over standard windows (local currency, %), excess vs home index, drawdown, volatility."""
    rows = {}
    for t in tickers:
        s = px[t].dropna()
        if s.empty:
            continue
        end = s.index[-1]
        windows = {"1개월": end - pd.DateOffset(months=1), "3개월": end - pd.DateOffset(months=3),
                   "6개월": end - pd.DateOffset(months=6), "연초 이후": pd.Timestamp(end.year - 1, 12, 31),
                   "1년": end - pd.DateOffset(years=1)}
        row = {k: _ret(s, v) for k, v in windows.items()}
        bm = benchmark_for(t)
        if bm and bm[0] in px:
            row["지수"] = bm[1]
            # index measured over the same dates as the stock (a halted stock stops at its last trade)
            row["1년 지수 대비(%p)"] = row["1년"] - _ret(px[bm[0]][:end], windows["1년"])
        last_year = s[s.index > windows["1년"]]
        row["52주 고점 대비(%)"] = (s.iloc[-1] / last_year.max() - 1) * 100
        row["연율 변동성(%)"] = last_year.pct_change().std() * np.sqrt(252) * 100
        row["기준일"] = end.date()
        rows[t] = row
    return pd.DataFrame(rows).T


def rebased(px: pd.DataFrame, tickers: list[str], months: int = 12) -> pd.DataFrame:
    """Prices rebased to 100 at the start of the window (each series from its own first valid day)."""
    start = px.index[-1] - pd.DateOffset(months=months)
    w = px.loc[px.index >= start, tickers].ffill()
    return w / w.bfill().iloc[0] * 100


def valuation(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    f = _cache_dir(None) / "valuation.json"
    cached = json.loads(f.read_text(encoding="utf-8")) if f.exists() and not refresh else {}
    todo = [t for t in tickers if t not in cached]
    if todo:
        import yfinance as yf
        from .net import yf_session

        s = yf_session()
        for t in todo:
            try:
                info = yf.Ticker(t, session=s).info or {}
            except Exception as e:
                print(f"skip {t}: {e}")
                info = {}
            cached[t] = {k: info.get(k) for k in VALUATION_FIELDS}
        f.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
    df = pd.DataFrame({t: cached[t] for t in tickers}).T.rename(columns=VALUATION_FIELDS)
    for c in df.columns:
        if c != "통화":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def news(tickers: list[str], per_ticker: int = 3, refresh: bool = False) -> pd.DataFrame:
    """Latest headlines per ticker (title, date, publisher, link). Headlines are leads, not facts."""
    f = _cache_dir(None) / "news.json"
    cached = json.loads(f.read_text(encoding="utf-8")) if f.exists() and not refresh else {}
    todo = [t for t in tickers if t not in cached]
    if todo:
        import yfinance as yf
        from .net import yf_session

        s = yf_session()
        for t in todo:
            items = []
            try:
                raw = yf.Ticker(t, session=s).news or []
            except Exception as e:
                print(f"skip {t}: {e}")
                raw = []
            for n in raw:
                c = n.get("content", n)  # yfinance has used two shapes over time
                items.append({
                    "title": c.get("title"),
                    "date": (c.get("pubDate") or c.get("displayTime") or "")[:10]
                            or (str(pd.to_datetime(n["providerPublishTime"], unit="s").date())
                                if n.get("providerPublishTime") else ""),
                    "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
                    "url": (c.get("canonicalUrl") or {}).get("url") or c.get("link"),
                })
            cached[t] = [i for i in items if i["title"]]
        f.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = [{"ticker": t, **i} for t in tickers for i in sorted(cached.get(t, []), key=lambda i: i["date"] or "", reverse=True)[:per_ticker]]
    return pd.DataFrame(rows, columns=["ticker", "date", "title", "publisher", "url"])
