import nbformat as nbf
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "notebooks"
OUT.mkdir(exist_ok=True)
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

SETUP = '''import sys, json, re
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))

import pandas as pd
import matplotlib.pyplot as plt
from IPython.display import display, Markdown
from research import financials as fin, playbook

pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
plt.rcParams.update({"font.family": ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"],
                     "axes.unicode_minus": False, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#52514e", "axes.labelcolor": "#52514e", "xtick.color": "#52514e",
                     "ytick.color": "#52514e", "axes.grid": True, "grid.color": "#e6e5e1", "grid.linewidth": 0.6,
                     "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb"})
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"   # 범주 색 고정 순서'''

# ---------------- 01 fundamentals ----------------
nb1 = nbf.v4.new_notebook()
nb1.cells = [
md('''# 01. 재무제표로 사업 읽기: 아시아 네오클라우드

**목표:** 재무제표 숫자를 받아 와서 지표로 바꾸고, 그 숫자가 던지는 질문을 찾는다. 결론은 내리지 않는다. 결론은 [02_debate.ipynb](02_debate.ipynb)에서 근거를 들고 토론하면서 다듬는다.

**네오클라우드란:** GPU 서버를 대량으로 사서 AI 학습·추론용 연산을 빌려주는 클라우드 사업자. 기존 클라우드보다 **자본 집약도가 훨씬 높아서** 재무제표에서 봐야 할 곳이 분명하다.

| 질문 | 보는 지표 |
|---|---|
| 수요가 실제로 늘고 있나 | 매출 성장률 |
| 투자한 GPU가 돈을 버나 | 유형자산 회전율, ROIC |
| 감가상각이 이익을 얼마나 깎나 | 감가상각비/매출, 매출총이익률 |
| 투자를 무엇으로 하나 | 설비투자/매출, FCF, 순차입금/EBITDA, 발행주식수 |
| 이익이 현금으로 들어오나 | 영업현금흐름/순이익 |

**데이터:** Yahoo Finance(yfinance) 연간 재무제표, `data/cache/`에 저장해서 다시 실행해도 같은 숫자가 나온다. 중요한 숫자는 회사 공시(일본 EDINET 유가증권보고서, 한국 DART, 대만 MOPS)와 반드시 대조할 것.'''),
code(SETUP),
md('''## 0. 대상 종목

- **사쿠라인터넷 (3778.T)**: 일본 정부의 '거버먼트 클라우드' 사업자이자, 경제산업성 보조금을 받아 GPU 클라우드를 대규모로 증설 중인 일본 대표 네오클라우드
- 비교 대상은 완벽한 동종 업체가 아니다. 순수 상장 네오클라우드가 아시아에 드물어서, 사업 일부가 겹치는 인터넷 인프라 기업을 놓고 **어디가 다른지**를 보는 용도로 쓴다.

`TICKER`를 바꾸면 다른 종목도 같은 방식으로 분석된다 (한국 `.KS`/`.KQ`, 대만 `.TW`, 홍콩 `.HK`).'''),
code('''TICKER = "3778.T"
PLAYBOOK = "neocloud"                    # playbooks/neocloud.yaml (다른 업종: "generic" 또는 새 YAML)
PEERS = ["3778.T", "3774.T", "9449.T"]   # 사쿠라인터넷, IIJ, GMO인터넷그룹
pb = playbook.load(PLAYBOOK)
print("플레이북:", pb["name"], "-", pb["description"])
REFRESH = False                          # True: Yahoo에서 새로 받기, False: data/cache 사용

data = fin.fetch(TICKER, refresh=REFRESH)
info = data["info"]
CUR = info.get("financialCurrency") or info.get("currency") or ""
print(info.get("longName"), "|", info.get("industry"), "|", info.get("country"), "| 통화:", CUR)
print((info.get("longBusinessSummary") or "")[:600])'''),
md('''## 1. 원재료: 손익·재무상태·현금흐름 주요 항목 (단위: 억, 회사 통화)

`FY2026(03)` = 2026년 3월에 끝난 회계연도. 일본 기업은 3월 결산이 많아서 한국·대만 기업(12월 결산)과 비교할 때 기간이 9개월 어긋난다는 점에 주의.'''),
code('''items = fin.line_items(data)
(items / 1e8).T.style.format("{:,.1f}", na_rep="-")'''),
md('''## 2. 지표: 숫자를 사업의 언어로

각 지표 옆에 **이 숫자가 무엇을 말해 주는지**를 같이 적었다. 지표 코드(`GM`, `CAPEX_REV` …)는 02 노트북에서 근거 id(`GM_FY2026`)로 그대로 쓰인다.'''),
code('''r = fin.ratios(items)
guide = pd.DataFrame(fin.METRICS, index=["지표", "단위", "무엇을 말하나"]).T
table = r[pb["metrics"]].T.copy()
table.insert(0, "무엇을 말하나", guide["무엇을 말하나"])
table.insert(0, "지표", guide["지표"])
table.drop(index="REV").style.format({c: "{:,.2f}" for c in r.index}, na_rep="-")'''),
md('''## 3. 그림으로 보기

한 차트에 축 하나만 쓴다. 단위가 다른 숫자는 다른 차트로 나눴다.'''),
code('''import numpy as np
x = np.arange(len(r.index)); labels = list(r.index)
fig, ax = plt.subplots(2, 2, figsize=(12, 8))

a = ax[0, 0]; w = 0.38
a.bar(x - w/2, items["revenue"] / 1e8, w, color=BLUE, label="매출")
a.bar(x + w/2, -items["capex"] / 1e8, w, color=ORANGE, label="설비투자")
a.set_title(f"매출 vs 설비투자 (억 {CUR})", loc="left"); a.legend(frameon=False)

a = ax[0, 1]
for col, c, name in (("GM", BLUE, "매출총이익률"), ("EBITDA_M", ORANGE, "EBITDA 마진"), ("OPM", AQUA, "영업이익률")):
    a.plot(x, r[col], color=c, lw=2, marker="o", ms=6, label=name)
    a.annotate(f"{r[col].iloc[-1]:.1f}%", (x[-1], r[col].iloc[-1]), xytext=(6, 0), textcoords="offset points", va="center", color="#52514e")
a.axhline(0, color="#52514e", lw=0.8); a.set_title("수익성 (%)", loc="left"); a.legend(frameon=False)

a = ax[1, 0]
a.bar(x - w/2, items["cfo"] / 1e8, w, color=BLUE, label="영업현금흐름")
a.bar(x + w/2, items["fcf"] / 1e8, w, color=ORANGE, label="FCF")
a.axhline(0, color="#52514e", lw=0.8); a.set_title(f"현금은 어디로 (억 {CUR})", loc="left"); a.legend(frameon=False)

a = ax[1, 1]
a.plot(x, r["PPE_TURN"], color=BLUE, lw=2, marker="o", ms=6)
for xi, v in zip(x, r["PPE_TURN"]):
    a.annotate(f"{v:.2f}x", (xi, v), xytext=(0, 8), textcoords="offset points", ha="center", color="#52514e")
a.set_title("유형자산 회전율 (매출/순유형자산, x)", loc="left"); a.margins(y=0.15)

for a in ax.flat:
    a.set_xticks(x, labels)
fig.suptitle(f"{info.get('longName')} ({TICKER})", x=0.01, ha="left", fontsize=14)
fig.tight_layout(); plt.show()'''),
md('''## 4. 숫자가 던지는 질문

섹터 플레이북([playbooks/](../playbooks/))의 규칙으로 자동 생성한 질문이다. 기준을 바꾸려면 코드가 아니라 YAML을 고친다. **답이 아니라 질문**이다. 각 질문은 공시·실적발표에서 답을 찾아야 하고, 답을 찾으면 그게 투자 논거나 반대 논거의 재료가 된다.

- 🔴 red: 투자 논거가 성립하려면 반드시 답해야 하는 것
- 🟡 yellow: 원인을 설명할 수 있어야 하는 변화'''),
code('''qs = fin.questions(r, playbook=pb)
icon = {"red": "🔴", "yellow": "🟡"}
display(Markdown("\\n".join(f"- {icon[q['level']]} `{q['evidence_id']}` {q['question']}" for q in qs) or "플래그 없음"))'''),
md('''## 5. 비교: 같은 '인터넷 인프라'인데 무엇이 다른가

최근 회계연도 기준. 결산월이 다르면(12월 vs 3월) 같은 시점이 아니다. 비교 대상의 사업 구성이 다르다는 점(예: GMO는 결제·금융 사업 포함)을 감안해서 읽을 것.'''),
code('''peers = fin.peer_table(PEERS, refresh=REFRESH, metrics=pb["metrics"])
peers.T'''),
md('''## 6. 숫자 → 이야기: 내 해석 쓰기

위 표·차트·질문을 보고 **내 말로** 아래 세 가지를 써 본다. 이 문장들이 02 노트북의 첫 번째 논거가 된다. 근거로 쓰는 지표는 `[GM_FY2026]`처럼 id를 붙인다.

1. **이 회사는 지금 어떤 국면인가?** (예: 수확기 / 투자기 / 구조조정기)
2. **시장은 무엇을 믿고 있고, 숫자 중 무엇이 그 믿음과 맞거나 어긋나나?**
3. **위 질문 중 내 논거를 무너뜨릴 수 있는 것은 무엇인가?**

> 여기에 직접 작성하세요.'''),
code('''# 02 노트북이 쓸 근거 파일 저장
ev = fin.evidence(r, CUR, metrics=pb["metrics"])
out = ROOT / "data" / "evidence"; out.mkdir(parents=True, exist_ok=True)
(out / f"{TICKER}.json").write_text(json.dumps({"ticker": TICKER, "company": info.get("longName"), "currency": CUR,
                                               "playbook": PLAYBOOK, "evidence": ev, "questions": qs}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"근거 {len(ev)}개, 질문 {len(qs)}개 저장 -> data/evidence/{TICKER}.json")'''),
]

# ---------------- 02 debate ----------------
nb2 = nbf.v4.new_notebook()
nb2.cells = [
md('''# 02. 투자 논거 vs 반대 논거 토론

**나는 BULL(매수 논거를 제시하는 애널리스트), AI는 BEAR(의심 많은 PM).** 토론이 끝나면 토론에 참여하지 않은 **독립 심판**이 양쪽의 근거 규율을 채점한다.

### 규칙 (코드가 강제함)
- 근거는 01 노트북이 만든 재무 지표(`GM_FY2026` 등)와, 있으면 `run.py`로 공시 원문에서 뽑은 팩트(`F1`, `F2` …)뿐이다.
- AI의 반박은 **근거 id를 반드시 인용**해야 한다. 인용이 없거나 존재하지 않는 id를 쓰면 코드가 그 반박을 **걸러내고 따로 표시**한다.
- 내가 `[id]`로 인용한 근거도 똑같이 검사한다.
- 01에서 나온 경고 질문이 BEAR와 심판에게 전달된다. 🔴 경고 중 토론에서 아무도 근거로 다루지 않은 것은 심판의 '풀리지 않은 질문'에 자동으로 추가된다.
- 밸류에이션·주가는 근거에 없다. AI가 지어내지 못하게 막아 두었다.
- **최종 판단은 사람이 한다.** 이 노트북의 결과물은 '토론 전 관점 → 토론 → 토론 후 관점'의 기록이다.

### 실행 모드
- `ANTHROPIC_API_KEY`가 있으면(환경변수 또는 저장소 루트의 `.env`) Claude API로 실제 토론한다.
- 없으면 **연습 모드**: 가짜 응답으로 흐름과 자동 검증만 확인한다.'''),
code(SETUP + '''
from research.debate import Debate, load_filing_facts

TICKER = "3778.T"
COMPANY = "SAKURA internet (さくらインターネット)"

ev_file = ROOT / "data" / "evidence" / f"{TICKER}.json"
if not ev_file.exists():
    raise FileNotFoundError("01_fundamentals.ipynb를 먼저 실행하세요.")
saved = json.loads(ev_file.read_text(encoding="utf-8"))
evidence = saved["evidence"] + load_filing_facts(TICKER.split(".")[0])

pb = playbook.load(saved.get("playbook", "generic"))
d = Debate(COMPANY, TICKER, evidence, language="Korean", bear_notes=playbook.bear_notes_text(pb),
           questions=saved["questions"])
print("모드:", "연습 모드 (API 호출 없음)" if d.dry_run else "Claude API", "| 근거 수:", len(d.evidence))'''),
md('''## 근거 목록

토론에서 인용할 수 있는 id들이다. 최근 2개년만 보여 준다 (전체는 `d.evidence`).'''),
code('''recent = [k for k in d.evidence if k.endswith(("FY2025", "FY2026")) or re.fullmatch(r"F[0-9]+", k)]
pd.DataFrame({"근거": [d.evidence[k] for k in recent]}, index=recent)'''),
md('''## 01에서 찾은 질문들 (토론 재료)'''),
code('''icon = {"red": "🔴", "yellow": "🟡"}
display(Markdown("\\n".join(f"- {icon[q['level']]} `{q['evidence_id']}` {q['question']}" for q in saved["questions"])))'''),
md('''## 토론 전 내 관점

한두 문장. 토론이 끝난 뒤 이것과 비교한다.'''),
code('''VIEW_BEFORE = """(예시. 본인 관점으로 바꾸세요)
사쿠라인터넷은 정부 지원을 받는 일본 유일 규모의 국산 GPU 클라우드로, 지금의 이익 악화는 선행 투자에 따른 감가상각 때문이며 가동률이 올라오면 마진이 회복된다."""'''),
md('''## Round 1: 매수 논거 제시

**쓰는 법:** 주장마다 근거 id를 `[ ]`로 붙인다. 강한 논거는 '좋아 보인다'가 아니라 *시장이 무엇을 놓치고 있고, 무엇이 그 간극을 메울지*를 말한다.'''),
code('''pitch1 = """(예시. 본인 논거로 바꾸세요)
1) 매출은 2년 동안 크게 늘었다 [REV_G_FY2025][REV_G_FY2026]. GPU 수요가 실제로 매출로 잡히고 있다.
2) 영업이익 적자는 감가상각 부담 때문이고 [DA_REV_FY2026], 감가상각 전 이익인 EBITDA 마진은 아직 20%대다 [EBITDA_M_FY2026].
3) 설비투자가 매출보다 크지만 [CAPEX_REV_FY2026] 성장 투자 단계이므로 정상이다 [CAPEX_DA_FY2026]."""

r1 = d.bull(pitch1)
display(Markdown(d.round_md(r1, 1)))'''),
md('''## Round 2: 가장 어려운 질문에 답하기

위에서 BEAR가 던진 **가장 어려운 질문**과 반박에 답한다. 답할 수 없으면 '모른다, 이 자료가 필요하다'고 쓰는 것도 좋은 답이다. 실제 투자위원회에서 그게 더 신뢰를 얻는다.'''),
code('''pitch2 = """(예시. BEAR의 질문을 읽고 본인 답으로 바꾸세요)
FCF가 크게 마이너스인 건 맞다 [FCF_M_FY2026]. 다만 2025년 증자로 자기자본을 늘렸고 [SHARE_G_FY2025] 부채비율은 아직 1배 수준이다 [DEBT_EQUITY_FY2026].
가동률과 계약 기간은 재무제표로 확인할 수 없다. 유가증권보고서와 실적발표 Q&A에서 확인해야 한다."""

r2 = d.bull(pitch2)
display(Markdown(d.round_md(r2, 2)))'''),
md('''라운드를 더 하려면 아래 셀을 복사해서 쓰면 된다.

```python
r3 = d.bull("""...""")
display(Markdown(d.round_md(r3, 3)))
```

## 독립 심판 평가

토론에 참여하지 않은 별도의 호출이 양쪽 주장을 근거 기준으로 판정한다. 심판이 인용한 id도 코드가 다시 검사한다.'''),
code('''j = d.judge()
display(Markdown(d.judge_md()))'''),
md('''## 논증 지도

토론 전체를 한 장으로 본다. 내 주장과 BEAR 반박이 **어떤 근거에 걸려 있는지**, 코드가 걸러낸 반박, 심판 판정, 아무도 다루지 않은 🔴 경고가 한눈에 보인다. (JupyterLab 4.1 이상과 GitHub에서 그림으로 보인다.)'''),
code('''display(Markdown(d.argument_map_md()))'''),
md('''## 토론 후 내 관점, 그리고 저장

**무엇이 바뀌었고 왜 바뀌었는지**를 쓴다. 바뀐 게 없다면 어떤 반박을 왜 받아들이지 않았는지를 쓴다. 이 기록이 이 프로젝트의 핵심 산출물이다.'''),
code('''VIEW_AFTER = """(예시. 본인 생각으로 바꾸세요)
감가상각 때문이라는 설명만으로는 부족하다. 유형자산 회전율이 계속 떨어지고 있어서 [PPE_TURN_FY2026] '가동률이 오르면 회복'이라는 가정을 숫자로 확인하기 전까지는 확신도를 낮춘다. 다음 단계: 유가증권보고서에서 GPU 설비 가동률·고객 계약 기간·보조금 회계 처리 확인."""

path = d.save(VIEW_BEFORE, VIEW_AFTER)
print("저장:", path.relative_to(ROOT))
# outputs/는 git에 올라가지 않으므로, GitHub에서 볼 수 있는 예시 사본을 docs/examples/에 둔다
example = ROOT / "docs" / "examples" / f"{TICKER.split('.')[0]}_debate{'_dryrun' if d.dry_run else ''}.md"
example.parent.mkdir(parents=True, exist_ok=True)
example.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
print("GitHub용 사본:", example.relative_to(ROOT))'''),
]


# ---------------- 03 market ----------------
nb3 = nbf.v4.new_notebook()
nb3.cells = [
md("""# 03. 시황: 밸류체인 전체를 한눈에

**목표:** 01·02가 한 기업을 깊게 본다면, 여기서는 **같은 밸류체인에 있는 기업들의 주가·밸류에이션·뉴스**를 넓게 본다. "시장은 지금 무엇을 믿고 있나"를 숫자로 확인하는 단계다.

- 종목 목록은 플레이북의 `universe`에서 온다 ([playbooks/neocloud.yaml](../playbooks/neocloud.yaml)). 역할별 분류는 스크리닝용 작업 가정이다.
- 수익률은 **현지 통화 기준**, 각 종목의 자국 지수와 비교한다 (일본 Nikkei 225, 한국 KOSPI, 대만 TAIEX, 홍콩 Hang Seng).
- 데이터는 Yahoo Finance. 그날 받은 값을 `data/cache/market/<날짜>/`에 저장해서 같은 날 다시 실행하면 같은 숫자가 나온다.
- **주의:** Yahoo 밸류에이션 값은 기준이 제각각이고 가끔 틀린다. 투자 판단의 입력이 아니라 '어디를 더 볼지' 고르는 용도다."""),
code(SETUP + """
from research import market as mk, playbook

PLAYBOOK = "neocloud"
REFRESH = False          # True: 오늘 데이터를 새로 받기
pb = playbook.load(PLAYBOOK)
groups = pb["universe"]
tickers = [t for g in groups.values() for t in g]
names = {t: n for g in groups.values() for t, n in g.items()}
role = {t: r for r, g in groups.items() for t in g}
px = mk.prices(tickers, refresh=REFRESH)
print(f"{len(tickers)}개 종목, 기준일 {px.index[-1].date()}")"""),
md("""## 1. 주가 성과

`1년 지수 대비(%p)`가 음수면 자국 시장보다 못했다는 뜻이다. `52주 고점 대비`는 고점에서 얼마나 내려와 있는지, `연율 변동성`은 주가가 얼마나 크게 흔들리는지를 보여 준다."""),
code("""perf = mk.performance(px, tickers)
perf.insert(0, "역할", [role[t] for t in perf.index])
perf.insert(0, "기업", [names[t] for t in perf.index])
num = [c for c in perf.columns if c not in ("기업", "역할", "지수", "기준일")]
perf[num] = perf[num].astype(float)
perf.style.format({c: "{:+.1f}" for c in num}, na_rep="-").background_gradient(
    subset=["1년 지수 대비(%p)"], cmap="RdBu", vmin=-60, vmax=60)"""),
md("""## 2. 역할별 주가 흐름 (1년 전 = 100)

역할마다 차트 하나, 축 하나. 회색 점선은 해당 그룹 첫 종목의 자국 지수다."""),
code("""COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
rb = mk.rebased(px, list(px.columns), months=12)
n = len(groups); cols = 2; rows = -(-n // cols)
fig, axes = plt.subplots(rows, cols, figsize=(13, 3.8 * rows), squeeze=False)
for a, (g, members) in zip(axes.flat, groups.items()):
    for c, t in zip(COLORS, members):
        a.plot(rb.index, rb[t], color=c, lw=2, label=names[t])
    bm = mk.benchmark_for(list(members)[0])
    if bm and bm[0] in rb:
        a.plot(rb.index, rb[bm[0]], color="#8a8985", lw=1.5, ls="--", label=bm[1])
    a.axhline(100, color="#52514e", lw=0.8)
    a.set_title(g, loc="left"); a.legend(frameon=False, fontsize=9, loc="upper left")
for a in list(axes.flat)[n:]:
    a.set_visible(False)
fig.tight_layout(); plt.show()"""),
md("""## 3. 밸류에이션 스냅샷

시가총액은 현지 통화. 적자 기업은 PER이 비어 있거나 음수다. 🔎 표시는 값이 비정상적으로 커서(PER 100배 초과 또는 음수) **원 자료 확인이 필요한 칸**이다."""),
code("""val = mk.valuation(tickers, refresh=REFRESH)
val.insert(0, "역할", [role[t] for t in val.index])
val.insert(0, "기업", [names[t] for t in val.index])
val["시가총액(억)"] = val.pop("시가총액") / 1e8
odd = lambda v: "🔎" if pd.notna(v) and (v > 100 or v < 0) else ""
for c in ("PER(지난 12개월)", "PER(예상)"):
    val[c + " 확인"] = val[c].map(odd)
val.style.format({c: "{:,.1f}" for c in val.columns if val[c].dtype.kind == "f"}, na_rep="-")"""),
md("""### 역할별 중앙값

같은 역할 안에서 비교해야 의미가 있다. 서버 제조사와 클라우드 운영사의 PER을 바로 비교하면 안 된다."""),
code("""cols_ = ["PER(지난 12개월)", "PER(예상)", "PBR", "EV/EBITDA", "EV/매출"]
clean = val[cols_].where((val[cols_] > 0) & (val[cols_] < 100))  # 🔎 값은 빼고
clean.groupby(val["역할"]).median().style.format("{:.1f}", na_rep="-")"""),
md("""## 4. 최근 헤드라인

Yahoo가 종목에 연결한 기사다. **종목과 직접 관련 없는 기사도 섞여 있다** (예: 업계 일반 기사). 헤드라인은 단서일 뿐이고, 사실 확인은 원문과 공시로 한다."""),
code("""nw = mk.news(tickers, per_ticker=3, refresh=REFRESH)
nw.insert(1, "기업", nw["ticker"].map(names))
esc = lambda x: str(x or "").replace("|", "/")
lines = ["| 기업 | 날짜 | 헤드라인 | 출처 |", "|---|---|---|---|"]
lines += [f"| {esc(r.기업)} | {esc(r.date)} | [{esc(r.title)}]({r.url}) | {esc(r.publisher)} |" if r.url
          else f"| {esc(r.기업)} | {esc(r.date)} | {esc(r.title)} | {esc(r.publisher)} |" for r in nw.itertuples()]
display(Markdown(chr(10).join(lines)))"""),
md("""## 5. 읽고 나서 적을 것

1. **시장은 이 밸류체인의 어디에 돈을 걸고 있나?** (어느 역할이 지수를 이기고, 어디가 지고 있나)
2. **내 관심 종목은 같은 역할 안에서 비싼가, 싼가? 그 차이를 01의 재무 숫자로 설명할 수 있나?**
3. **주가가 크게 움직인 종목은 무슨 일이 있었나?** (헤드라인은 단서, 확인은 공시로)

> 여기에 직접 작성하세요."""),
]

for nb, name in ((nb1, "01_fundamentals.ipynb"), (nb2, "02_debate.ipynb"), (nb3, "03_market.ipynb")):
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, OUT / name)
print("ok")
