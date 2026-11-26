"""Investment journal: why I bought (public) and what happened (private), checked against the numbers.

    journal/theses/<ticker>.yaml   public. Thesis in my own words, the debate it came from, kill
                                   criteria ("I am wrong if ..."), open questions, dated reviews.
    journal/trades.csv             private (.gitignore). date,ticker,side,qty,price[,fee][,currency]
                                   side: buy / sell / split (split: qty = new shares per old share)
                                   Rows on the same date run in file order.

A position is tracked in rounds: a round opens with the first buy and closes when the quantity
returns to zero, so selling out and buying back later starts a new round with its own return.
Round return = (sell proceeds + current value) / total buy cost - 1, fees included, local currency.
Money recycled inside a round (buy, partial sell, buy again) counts in the cost every time, which
understates the return of very active rounds; this is a journal, not a performance-attribution tool.
The index comparison replays the same cash flows into the home index: every buy buys the index with
the same money on the same day, every sell sells the same fraction of the index units.

Two reports:
    docs/portfolio.md              public: weights and returns in %, never quantities, prices or amounts
    outputs/portfolio_private.md   private: everything, amounts in the base currency

Error messages name the row and the field, never a quantity or price: the 04 notebook's outputs
are committed, and an error is saved in them.
"""
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import financials as fin
from .market import benchmark_for

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "journal"
THESES = JOURNAL / "theses"
TRADES = JOURNAL / "trades.csv"
EXAMPLES = JOURNAL / "examples"  # used while there is neither journal/trades.csv nor a thesis file
EXAMPLE_TRADES = EXAMPLES / "trades.csv"

CURRENCY_BY_SUFFIX = {
    ".T": "JPY", ".KS": "KRW", ".KQ": "KRW", ".TW": "TWD", ".TWO": "TWD", ".HK": "HKD",
    ".SS": "CNY", ".SZ": "CNY", ".SI": "SGD", ".KL": "MYR", ".BK": "THB", ".JK": "IDR",
    ".NS": "INR", ".BO": "INR",
}
STATUSES = ("watching", "holding", "closed")
STATUS_KO = {"watching": "관심", "holding": "보유", "closed": "정리"}
TRADE_COLUMNS = ["date", "ticker", "side", "qty", "price", "fee", "currency"]
SPLIT_SUSPECT = (0.2, 5.0)  # current price / average cost outside this: probably an unrecorded split


# ---------- loading ----------
def currency_for(ticker: str) -> str:
    for suffix, cur in sorted(CURRENCY_BY_SUFFIX.items(), key=lambda kv: -len(kv[0])):
        if ticker.endswith(suffix):
            return cur
    return "USD"  # no suffix: Yahoo's US listings


def _num(s: pd.Series) -> pd.Series:
    """Numbers as typed in a broker export: '1,000' -> 1000. Unparseable -> NaN (then rejected)."""
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.strip().replace({"": None, "nan": None}),
                         errors="coerce")


def load_trades(path: Path) -> pd.DataFrame:
    """Trades sorted by date (same date: file order). Raises with row numbers only, never amounts."""
    t = pd.read_csv(path, dtype=str, comment="#", skipinitialspace=True, keep_default_na=False)
    missing = {"date", "ticker", "side", "qty", "price"} - set(t.columns)
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
    if t.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    rowno = t.index + 2  # line number in the file: header is line 1 (comment lines not counted)
    t["date"] = pd.to_datetime(t["date"], errors="coerce")
    t["ticker"] = t["ticker"].str.strip().str.upper()
    t["side"] = t["side"].str.strip().str.lower()
    t["qty"] = _num(t["qty"])
    t["price"] = _num(t["price"])
    t["fee"] = _num(t["fee"]).fillna(0.0) if "fee" in t else 0.0
    cur = t["currency"].str.strip().str.upper() if "currency" in t else pd.Series("", index=t.index)
    t["currency"] = cur.where(cur != "", t["ticker"].map(currency_for))
    trade = t["side"].isin(["buy", "sell"])
    problems = {
        "date is not a date": t["date"].isna(),
        "ticker is empty": t["ticker"] == "",
        "side must be buy, sell or split": ~t["side"].isin(["buy", "sell", "split"]),
        "qty must be a number > 0": ~(t["qty"] > 0),
        "price must be a number > 0": trade & ~(t["price"] > 0),
        "fee must be a number >= 0": ~(t["fee"] >= 0),
    }
    bad = [f"row {n}: {msg}" for msg, mask in problems.items() for n in rowno[mask.to_numpy()]]
    if bad:
        raise ValueError(f"{path.name}: " + "; ".join(sorted(bad, key=lambda s: int(s.split()[1][:-1]))))
    t["row"] = rowno
    return t.sort_values("date", kind="stable").reset_index(drop=True)


def _validate_thesis(th: dict, path: Path):
    for k in ("ticker", "company", "status", "thesis"):
        if not th.get(k):
            raise ValueError(f"{path.name}: '{k}' is required")
    if th["ticker"] != path.stem:
        raise ValueError(f"{path.name}: ticker '{th['ticker']}' does not match the file name")
    if th["status"] not in STATUSES:
        raise ValueError(f"{path.name}: status must be one of {STATUSES}")
    for k in ("kill_criteria", "open_questions", "reviews"):
        if not isinstance(th[k], list):
            raise ValueError(f"{path.name}: '{k}' must be a list (one '- ' item per line)")
    for c in th["kill_criteria"]:
        if not isinstance(c, dict):
            raise ValueError(f"{path.name}: kill criterion '{c}' must be 'text: ...' or 'metric: ... below/above: ...'")
        if "text" in c:
            continue
        ops = [op for op in ("below", "above") if op in c]
        v = c[ops[0]] if len(ops) == 1 else None
        if (c.get("metric") not in fin.METRICS or v is None or isinstance(v, bool)
                or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise ValueError(f"{path.name}: kill criterion {c} needs either 'text', or a metric from "
                             f"{list(fin.METRICS)} with exactly one numeric 'below' or 'above'")
    for rv in th["reviews"]:
        if not isinstance(rv, dict) or not rv.get("date") or not rv.get("note"):
            raise ValueError(f"{path.name}: every review needs a date and a note, got {rv}")


def load_theses(folder: Path | None = None) -> dict[str, dict]:
    folder = folder or THESES
    out = {}
    for p in sorted(folder.glob("*.yaml")):
        th = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        th["ticker"] = str(th.get("ticker") or "").strip().upper()  # 'ticker: 7203' loads as an int
        for k in ("kill_criteria", "open_questions", "reviews"):
            th[k] = [] if th.get(k) is None else th[k]  # 'reviews:' with nothing under it loads as None
        th["_file"] = p
        _validate_thesis(th, p)
        out[th["ticker"]] = th
    return out


def new_thesis(ticker: str, company: str, playbook: str = "generic", debate_json: Path | None = None,
               folder: Path | None = None) -> Path:
    """Write a thesis skeleton. With a saved debate (outputs/debates/*.json), the view after the debate
    becomes the draft thesis and the judge's unresolved questions become the open questions."""
    folder = folder or THESES
    ticker = ticker.strip().upper()
    path = folder / f"{ticker}.yaml"
    if path.exists():
        raise FileExistsError(f"{path} already exists; edit it instead.")
    thesis, questions, debate = "(내 문장으로: 시장이 무엇을 놓치고 있고, 무엇이 그 간극을 메우나)", [], None
    if debate_json:
        import json
        d = json.loads(Path(debate_json).read_text(encoding="utf-8"))
        thesis = d.get("view_after") or thesis
        questions = [u.get("question", "") for u in (d.get("judgement") or {}).get("unresolved") or []]
        debate = Path(debate_json).with_suffix(".md").name
    doc = {
        "ticker": ticker, "company": company, "playbook": playbook, "status": "watching",
        "opened": date.today().isoformat(), "debate": debate, "thesis": thesis,
        "kill_criteria": [{"metric": "GM", "below": 0, "why": "(예시) 이 지표가 이 선을 넘으면 논거가 틀린 것"},
                          {"text": "(예시) 숫자로 못 재는 조건: 핵심 고객 계약 해지 등"}],
        "open_questions": questions, "reviews": [],
    }
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    return path


# ---------- rounds ----------
def rounds(trades: pd.DataFrame) -> list[dict]:
    """Group trades into rounds per ticker (average-cost method). Amounts in the trade currency.

    Each round keeps its cash flows (date, buy cost or fraction of shares sold) for the index replay.
    """
    out, cur = [], {}
    for t in trades.itertuples():
        where = f"row {getattr(t, 'row', '?')} ({t.ticker})"
        r = cur.get(t.ticker)
        if r is None:
            if t.side != "buy":
                raise ValueError(f"{where}: {t.side} with no shares held")
            r = cur[t.ticker] = {"ticker": t.ticker, "currency": t.currency, "opened": t.date, "closed": None,
                                 "qty": 0.0, "cost_open": 0.0, "buy_cost": 0.0, "proceeds": 0.0, "trades": 0,
                                 "flows": [], "splits": 0}
        if t.currency != r["currency"]:
            raise ValueError(f"{where}: currency differs from the earlier rows of this round")
        r["trades"] += 1
        if t.side == "split":
            r["qty"] *= t.qty  # cost unchanged, so the average cost falls by the same ratio
            r["splits"] += 1
        elif t.side == "buy":
            cost = t.qty * t.price + t.fee
            r["qty"] += t.qty
            r["cost_open"] += cost
            r["buy_cost"] += cost
            r["flows"].append((t.date, "buy", cost))
        else:
            if t.qty > r["qty"] + 1e-9:
                raise ValueError(f"{where}: sells more shares than held (a split not recorded? use side=split)")
            frac = t.qty / r["qty"]
            r["cost_open"] *= 1 - frac  # average cost: the sold shares leave at the average
            r["qty"] -= t.qty
            r["proceeds"] += t.qty * t.price - t.fee
            r["flows"].append((t.date, "sell", frac))
            if r["qty"] < 1e-9:
                r.update(qty=0.0, cost_open=0.0, closed=t.date)
                out.append(cur.pop(t.ticker))
    return out + list(cur.values())


def _period(start) -> str:
    years = (pd.Timestamp.today() - pd.Timestamp(start)).days / 365
    return "2y" if years < 1.9 else "5y" if years < 4.9 else "max"


def _fx_ticker(cur: str, base: str) -> str:
    return f"{cur}{base}=X"


def fetch_market(rs: list[dict], base: str, refresh: bool = False) -> pd.DataFrame:
    """Daily closes for every traded ticker, its home index and the FX rates to the base currency."""
    from . import market as mk

    tickers = sorted({r["ticker"] for r in rs})
    fx = sorted({_fx_ticker(r["currency"], base) for r in rs if r["currency"] != base})
    start = min(r["opened"] for r in rs)
    return mk.prices(tickers + fx, period=_period(start), refresh=refresh)


def fetch_splits(tickers: list[str]) -> dict[str, pd.Series] | None:
    """Split history per ticker from Yahoo (date -> ratio). None if Yahoo cannot be reached."""
    try:
        import yfinance as yf
        from .net import yf_session

        s = yf_session()
        out = {}
        for t in tickers:
            sp = yf.Ticker(t, session=s).splits
            sp.index = pd.to_datetime(sp.index).tz_localize(None) if len(sp) else pd.DatetimeIndex([])
            out[t] = sp
        return out
    except Exception as e:
        print("분할 이력을 받지 못함 (현재가/평균단가 비율로 대신 점검):", e)
        return None


def _split_suspect(r: dict, end, avg: float, last: float, splits: dict | None) -> bool:
    """A split Yahoo knows about inside the round, not recorded as side=split. Without Yahoo's split
    history: the current price is far from the average cost."""
    if splits is not None and r["ticker"] in splits:
        sp = splits[r["ticker"]]
        return int(((sp.index > r["opened"]) & (sp.index <= end)).sum()) > r["splits"]
    return bool(r["closed"] is None and avg > 0 and not pd.isna(last)
                and not SPLIT_SUSPECT[0] < last / avg < SPLIT_SUSPECT[1])


def _series(px: pd.DataFrame, col: str, asof) -> pd.Series:
    if col not in px:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = px[col].dropna()
    return s[s.index <= asof]


def _close_on(s: pd.Series, d) -> float:
    """Last close on or before d; NaN when the data starts after d (no silent shorter window)."""
    s = s[s.index <= d]
    return s.iloc[-1] if not s.empty else np.nan


def index_return(flows: list, idx: pd.Series, end, buy_cost: float) -> float:
    """The round's cash flows replayed into the index: same money in, same fraction out."""
    units, proceeds = 0.0, 0.0
    for d, kind, x in flows:
        p = _close_on(idx, d)
        if pd.isna(p):
            return np.nan
        if kind == "buy":
            units += x / p
        else:
            proceeds += units * x * p
            units *= 1 - x
    last = _close_on(idx, end)
    return ((proceeds + units * last) / buy_cost - 1) * 100 if buy_cost and not pd.isna(last) else np.nan


def evaluate(rs: list[dict], px: pd.DataFrame, base: str = "KRW", asof=None,
             splits: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """One row per round: return, index return over the same cash flows, weight (open rounds only).

    `splits`: Yahoo split history (fetch_splits) to catch splits missing from the trades file."""
    asof = pd.Timestamp(asof or (px.index[-1] if len(px) else date.today()))
    rows = []
    for r in rs:
        s = _series(px, r["ticker"], asof)
        last = s.iloc[-1] if not s.empty else np.nan
        open_ = r["closed"] is None
        value = r["qty"] * last if open_ else 0.0
        end = asof if open_ else r["closed"]
        ret = ((r["proceeds"] + value) / r["buy_cost"] - 1) * 100 if r["buy_cost"] else np.nan
        bm = benchmark_for(r["ticker"])
        bm_ret = index_return(r["flows"], _series(px, bm[0], asof), end, r["buy_cost"]) if bm else np.nan
        if r["currency"] == base:
            rate = 1.0
        else:
            f = _series(px, _fx_ticker(r["currency"], base), asof)
            rate = f.iloc[-1] if not f.empty else np.nan
        avg = r["cost_open"] / r["qty"] if r["qty"] else np.nan
        rows.append({
            "ticker": r["ticker"], "status": "holding" if open_ else "closed", "currency": r["currency"],
            "opened": r["opened"].date(), "closed": r["closed"].date() if r["closed"] is not None else None,
            "days": (end - r["opened"]).days, "trades": r["trades"],
            "qty": r["qty"], "avg_cost": avg, "last": last,
            "buy_cost": r["buy_cost"], "proceeds": r["proceeds"], "value": value,
            "value_base": value * rate if open_ else 0.0,
            "return_pct": ret, "index": bm[1] if bm else None, "index_pct": bm_ret, "excess_pp": ret - bm_ret,
            "split_suspect": _split_suspect(r, end, avg, last, splits),
        })
    cols = ["ticker", "status", "currency", "opened", "closed", "days", "trades", "qty", "avg_cost", "last",
            "buy_cost", "proceeds", "value", "value_base", "return_pct", "index", "index_pct", "excess_pp",
            "split_suspect"]
    df = pd.DataFrame(rows, columns=cols)
    held = df["status"] == "holding"
    # weights only when every open position has a value in the base currency: a missing FX rate or
    # price would otherwise silently re-spread 100% over the rest
    complete = held.any() and df.loc[held, "value_base"].notna().all()
    total = df.loc[held, "value_base"].sum()
    df["weight_pct"] = np.where(held & complete & (total > 0), df["value_base"] / (total or 1) * 100, np.nan)
    df.attrs.update(base=base, asof=asof.date())
    return df


# ---------- kill criteria ----------
def check_kill(thesis: dict, r: pd.DataFrame | None) -> list[dict]:
    """Each kill criterion against the latest reported value. status: hit / ok / manual / n/a."""
    out = []
    keys = fin.period_keys(list(r.index)) if r is not None else {}
    for c in thesis["kill_criteria"]:
        if "text" in c:
            out.append({"criterion": str(c["text"]), "status": "manual", "evidence": None, "why": c.get("why")})
            continue
        op = "below" if "below" in c else "above"
        name, unit, _ = fin.METRICS[c["metric"]]
        label = f"{name} {'<' if op == 'below' else '>'} {c[op]}{unit}"
        s = r[c["metric"]].dropna() if r is not None and c["metric"] in r else pd.Series(dtype=float)
        if s.empty:
            out.append({"criterion": label, "status": "n/a", "evidence": None, "why": c.get("why")})
            continue
        v, fy = float(s.iloc[-1]), s.index[-1]
        hit = v < c[op] if op == "below" else v > c[op]
        out.append({"criterion": label, "status": "hit" if hit else "ok", "value": v,
                    "evidence": f"{c['metric']}_{keys[fy]}", "text": f"{name} {fy}: {fin._fmt(v, unit)}",
                    "why": c.get("why")})
    return out


def kill_status(theses: dict, refresh: bool = False) -> dict[str, list[dict]]:
    """Kill criteria for every thesis, using cached statements (data/cache) unless refresh=True."""
    out = {}
    for t, th in theses.items():
        r = None
        if any("metric" in c for c in th["kill_criteria"]):
            try:
                r = fin.ratios(fin.line_items(fin.fetch(t, refresh=refresh)))
            except Exception as e:  # one ticker without data should not stop the report
                print(f"재무 데이터 없음 {t}: {e}")
        out[t] = check_kill(th, r)
    return out


# ---------- reports ----------
ICON = {"hit": "🔴", "ok": "🟢", "manual": "⚪", "n/a": "⚫"}


def _pct(v, signed=True) -> str:
    return "-" if v is None or pd.isna(v) else (f"{v:+.1f}%" if signed else f"{v:.1f}%")


def _cell(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ").strip()


def consistency(ev: pd.DataFrame, theses: dict) -> list[str]:
    """Bookkeeping the journal should never have. Messages carry no amounts (they are public)."""
    held_rows = ev[ev["status"] == "holding"]
    held = set(held_rows["ticker"])
    notes = [f"`{t}`: 보유 중인데 논거 파일이 없다 (journal/theses/{t}.yaml)" for t in sorted(held - set(theses))]
    notes += [f"`{t}`: 논거 상태는 '보유'인데 매매 기록상 보유 수량이 없다" for t, th in theses.items()
              if th["status"] == "holding" and t not in held]
    notes += [f"`{t}`: 보유 중인데 논거 상태가 '{STATUS_KO[th['status']]}'다" for t, th in theses.items()
              if th["status"] != "holding" and t in held]
    notes += [f"`{x.ticker}`: 현재가나 환율이 없어 평가할 수 없다. 이 종목 때문에 비중을 계산하지 않았다"
              for x in held_rows.itertuples() if pd.isna(x.value_base)]
    notes += [f"`{x.ticker}` ({x.opened} 시작 회차): 액면분할이 매매 기록에 없는 것 같다. 수익률이 틀릴 수 있다 (side=split 줄 추가)"
              for x in ev.itertuples() if x.split_suspect]
    return notes


def public_markdown(ev: pd.DataFrame, theses: dict, kills: dict, is_example: bool = False) -> str:
    """The page that goes on GitHub. Percentages only: no quantity, price, cost or value appears."""
    asof = ev.attrs.get("asof", date.today())
    name = lambda t: theses[t]["company"] if t in theses else t
    kill_icons = lambda t: " ".join(ICON[k["status"]] for k in kills.get(t, [])) or "-"
    L = ["# 투자 기록", "",
         f"*기준일 {asof} · 비중은 {ev.attrs.get('base', '')} 환산 평가액 기준 · 수익률은 현지 통화, 수수료 포함 · "
         "같은 기간 지수는 같은 날 같은 돈을 지수에 넣고 뺐을 때 · 수량·단가·금액은 공개하지 않습니다.*", ""]
    if is_example:
        L += ["> ⚠️ **예시 데이터입니다.** 실제 매매 기록(journal/trades.csv)과 논거 파일이 아직 없습니다.", ""]
    L += ["틀리면 팔 조건: 🔴 조건에 걸림 · 🟢 아직 아님 · ⚪ 숫자로 못 재는 조건(직접 확인) · ⚫ 데이터 없음", ""]

    hold = ev[ev["status"] == "holding"].sort_values("weight_pct", ascending=False)
    L += ["## 보유 중", "", "| 종목 | 비중 | 수익률 | 같은 기간 지수 | 지수 대비 | 보유일 | 팔 조건 |", "|---|---:|---:|---:|---:|---:|---|"]
    for x in hold.itertuples():
        L.append(f"| {_cell(name(x.ticker))} (`{x.ticker}`) | {_pct(x.weight_pct, False)} | {_pct(x.return_pct)} | "
                 f"{_pct(x.index_pct)} {_cell(x.index)} | {_pct(x.excess_pp).replace('%', '%p')} | {x.days} | {kill_icons(x.ticker)} |")
    if hold.empty:
        L.append("| (없음) | | | | | | |")

    watch = [t for t, th in theses.items() if th["status"] == "watching"]
    if watch:
        L += ["", "## 관심 종목 (논거만 있고 아직 사지 않음)", "", "| 종목 | 기록 시작 | 팔 조건 |", "|---|---|---|"]
        L += [f"| {_cell(name(t))} (`{t}`) | {theses[t].get('opened', '')} | {kill_icons(t)} |" for t in watch]

    closed = ev[ev["status"] == "closed"].sort_values("closed")
    if not closed.empty:
        L += ["", "## 정리한 회차", "", "| 종목 | 기간 | 수익률 | 같은 기간 지수 | 지수 대비 |", "|---|---|---:|---:|---:|"]
        L += [f"| {_cell(name(x.ticker))} (`{x.ticker}`) | {x.opened} → {x.closed} | {_pct(x.return_pct)} | "
              f"{_pct(x.index_pct)} {_cell(x.index)} | {_pct(x.excess_pp).replace('%', '%p')} |" for x in closed.itertuples()]

    notes = consistency(ev, theses)
    if notes:
        L += ["", "## 기록 점검", ""] + [f"- ⚠️ {n}" for n in notes]

    L += ["", "## 종목별 논거", ""]
    for t, th in theses.items():
        L += [f"### {_cell(th['company'])} (`{t}`) · {STATUS_KO[th['status']]}", "",
              f"*기록 시작 {th.get('opened', '-')} · 플레이북 `{th.get('playbook', 'generic')}`*", ""]
        L += ["> " + line for line in str(th["thesis"]).strip().splitlines()] + [""]
        if th.get("debate"):  # link the public copy (docs/examples/) when there is one
            public = ROOT / "docs" / "examples" / th["debate"]
            L += [f"토론 기록: [{th['debate']}](examples/{th['debate']})" if public.exists()
                  else f"토론 기록: `{th['debate']}` (로컬 outputs/debates/)", ""]
        if kills.get(t):
            L += ["**틀리면 팔 조건**", ""]
            for k in kills[t]:
                found = f" — 최신값 `{k['evidence']}` {k['text']}" if k.get("evidence") else ""
                why = f" ({k['why']})" if k.get("why") else ""
                L.append(f"- {ICON[k['status']]} {_cell(k['criterion'])}{why}{found}")
            L.append("")
        if th["open_questions"]:
            L += ["**아직 답하지 못한 질문**", ""] + [f"- {_cell(q)}" for q in th["open_questions"]] + [""]
        if th["reviews"]:
            L += ["**복기**", ""] + [f"- {rv['date']}: {_cell(rv['note'])}" for rv in sorted(th["reviews"], key=lambda rv: str(rv["date"]))] + [""]
    return "\n".join(L).rstrip() + "\n"


def private_markdown(ev: pd.DataFrame) -> str:
    """Local-only detail: quantities, average cost and amounts. Never commit this file."""
    base = ev.attrs.get("base", "")
    num = lambda v, d=0: "-" if v is None or pd.isna(v) else f"{v:,.{d}f}"
    L = [f"# 투자 기록 (비공개, 기준일 {ev.attrs.get('asof', '')})", "",
         "| 종목 | 상태 | 수량 | 평균단가 | 현재가 | 매수 원금 | 매도 대금 | 평가액 | 평가액(" + base + ") | 수익률 | 비중 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for x in ev.itertuples():
        L.append(f"| `{x.ticker}` {x.currency} | {STATUS_KO[x.status]} | {num(x.qty, 2)} | {num(x.avg_cost, 2)} | {num(x.last, 2)} | "
                 f"{num(x.buy_cost)} | {num(x.proceeds)} | {num(x.value)} | {num(x.value_base)} | {_pct(x.return_pct)} | {_pct(x.weight_pct, False)} |")
    L += ["", f"**보유 평가액 합계 ({base}):** {num(ev['value_base'].sum())}"]
    return "\n".join(L) + "\n"


def sources() -> tuple[Path, Path, bool]:
    """(trades file, theses folder, is_example). Your own files win as soon as either exists, so a
    thesis written before the first trade shows up; the examples are used only when both are missing."""
    has_theses = any(THESES.glob("*.yaml"))
    if TRADES.exists() or has_theses:
        return TRADES, THESES, False
    return EXAMPLE_TRADES, EXAMPLES / "theses", True


def build(base: str = "KRW", refresh: bool = False, write: bool = True) -> dict:
    """Load trades and theses, price everything, check kill criteria, write both reports."""
    trades_path, theses_dir, is_example = sources()
    trades = load_trades(trades_path) if trades_path.exists() else pd.DataFrame(columns=TRADE_COLUMNS)
    theses = load_theses(theses_dir)
    rs = rounds(trades)
    px = fetch_market(rs, base, refresh=refresh) if rs else pd.DataFrame()
    splits = fetch_splits(sorted({r["ticker"] for r in rs})) if rs else {}
    ev = evaluate(rs, px, base=base, splits=splits)
    kills = kill_status(theses, refresh=refresh)
    pub = public_markdown(ev, theses, kills, is_example=is_example)
    out = {"trades": trades, "is_example": is_example, "theses": theses, "eval": ev, "kills": kills,
           "public_md": pub, "private_md": private_markdown(ev)}
    if write:
        (ROOT / "docs").mkdir(exist_ok=True)
        (ROOT / "outputs").mkdir(exist_ok=True)
        out["public_path"] = ROOT / "docs" / ("portfolio_example.md" if is_example else "portfolio.md")
        out["public_path"].write_text(pub, encoding="utf-8")
        out["private_path"] = ROOT / "outputs" / "portfolio_private.md"
        out["private_path"].write_text(out["private_md"], encoding="utf-8")
    return out
