# Asia equity research: 재무제표 → 질문 → 논거/반대 논거 토론

아시아 상장 기업을 대상으로 **재무제표를 읽고, 숫자가 던지는 질문을 찾고, 시황을 확인하고, 그 근거를 들고 AI PM과 투자 논거·반대 논거를 토론**하는 리서치 하네스입니다. 업종별 기준은 코드가 아니라 **섹터 플레이북(YAML)**에 있어서, 섹터를 가리지 않고 확장할 수 있습니다. 첫 플레이북은 아시아 네오클라우드(GPU 클라우드)입니다.

최종 투자 판단은 사람이 합니다. 이 도구는 근거 정리와 토론 상대 역할만 하며, 추천은 하지 않습니다. 토론의 근거에는 가격·밸류에이션이 들어가지 않습니다(시황 노트북은 사람이 보는 참고 자료).

## 구조
```
playbooks/*.yaml ──┐ (섹터별: 볼 지표, 경고 규칙, 반대 논거 체크포인트, 시황 종목군)
                   ▼
01 재무제표 → 지표 → 근거 저장소(id) → 숫자가 던지는 질문
                                │
run.py 공시 원문 → 인용 검증된 팩트(F1…) ┤
                                ▼
02 토론: 나(BULL) ↔ AI PM(BEAR) → 독립 심판 → 논증 지도 → 토론 전/후 내 관점 기록
03 시황: 밸류체인별 주가 성과·밸류에이션·헤드라인 (사람이 읽는 참고 자료)
```

| 파일 | 하는 일 | 코드로 하는 자동 검증 |
|---|---|---|
| [playbooks/](playbooks/) | `generic.yaml`(업종 공통) + `neocloud.yaml`·`semiconductor.yaml`(`extends: generic`). 지표 목록, 경고 규칙, BEAR가 파고들 섹터 특유의 함정, 시황 종목군 | 불러올 때 지표·규칙 유형·id 중복을 검사 |
| [notebooks/01_fundamentals.ipynb](notebooks/01_fundamentals.ipynb) | 연간 재무제표 수집(yfinance) → 지표 → 차트 → 플레이북 규칙으로 질문 생성 → 비교 기업 표 | 지표마다 근거 id(`GM_FY2026` 등), 중복 시 오류 |
| [notebooks/02_debate.ipynb](notebooks/02_debate.ipynb) | 내가 BULL, AI가 BEAR PM으로 토론 → 독립 심판이 채점 → 논증 지도(Mermaid) → 토론 전후 관점 저장. 예시: [docs/examples/3778_debate_dryrun.md](docs/examples/3778_debate_dryrun.md) | 근거 id가 없거나 틀린 반박·판정은 자동으로 걸러냄. 내가 인용한 id도 검사. 01의 🔴 경고 중 토론에서 다루지 않은 것은 '풀리지 않은 질문'에 자동 추가 |
| [notebooks/03_market.ipynb](notebooks/03_market.ipynb) | 밸류체인 역할별 주가 성과(자국 지수 대비), 1년 주가 흐름, 밸류에이션, 최근 헤드라인 | 이상한 밸류에이션 값(PER 100배 초과·음수)에 확인 표시 |
| [run.py](run.py) | 공시·실적발표 원문(.txt) → 팩트 추출 → 논거 초안 → 회의적 검토 | 인용문이 원문에 실제로 있는지 확인. 02에서 `F1`, `F2` … 근거로 재사용 |
| [research/](research/) | 공용 모듈: financials, playbook, market, debate, llm, net | |
| [prompts/](prompts/) | 01~03 run.py, 04 BEAR PM, 05 심판 | |
| [tests/](tests/) | 회귀 테스트 (`python -m pytest tests -q`), 네트워크 없이 실행 | |
| [tools/build_notebooks.py](tools/build_notebooks.py) | 노트북 세 개를 코드로 생성 | |

## 사용법
```bash
pip install -r requirements.txt
# API 키: 저장소 루트에 .env 파일을 만들고 ANTHROPIC_API_KEY=... 한 줄 (.env는 git에 올라가지 않음)
jupyter lab notebooks/      # 01 → 02 순서, 03은 아무 때나
```
- API 키 대신 Claude 구독을 쓰려면 `.env`에 `LLM_BACKEND=claude-code` 한 줄. 로그인된 Claude Code CLI(`claude`)로 호출하며 구독 사용량을 씁니다.
- 둘 다 없으면 02 노트북은 **연습 모드**(가짜 응답)로 돌아갑니다. 흐름과 자동 검증만 확인할 수 있습니다.
- 기본 모델은 `claude-opus-5-5`입니다. 환경변수 `ANTHROPIC_MODEL`로 바꿀 수 있습니다.
- 회사망처럼 HTTPS 인증서를 중간에서 바꾸는 네트워크에서는 Windows 신뢰 루트 인증서를 함께 신뢰합니다 ([research/net.py](research/net.py)).

### 새 섹터 추가하기
1. `playbooks/<섹터>.yaml`을 만들고 `extends: generic`
2. `metrics`에 추가로 볼 지표, `rules`에 경고 규칙(같은 `id`를 쓰면 generic 규칙을 덮어씀), `bear_notes`에 섹터 특유의 함정, `universe`에 시황 종목군
3. 01 노트북에서 `PLAYBOOK = "<섹터>"`

지표 계산식은 [research/financials.py](research/financials.py)의 `METRICS`/`ratios()`에 있습니다. 은행(NIM, 대손비용률)·리츠(FFO)처럼 지금 없는 지표가 필요한 섹터는 계산식부터 추가해야 합니다.

### 공시 원문 파이프라인
```bash
python run.py --company "SAKURA internet" --ticker 3778 --dir inputs/3778          # inputs/3778/ 에 .txt
python run.py --dry-run --company Test --ticker 0000 --dir inputs/demo             # API 없이 흐름만 테스트
```

## 한계
- Yahoo 데이터는 편의용입니다. 재무 숫자는 EDINET(일본)·DART(한국)·MOPS(대만) 원문과 대조해야 하고, 보통 4개년만 제공됩니다. 밸류에이션 값은 기준이 제각각이고 가끔 틀립니다(예: 비정상적인 예상 PER).
- Yahoo 헤드라인은 종목과 직접 관련 없는 기사도 섞여 있습니다.
- 근거 id 검증은 "그 id가 존재한다"까지만 보장합니다. 인용한 근거가 주장을 실제로 뒷받침하는지는 심판 단계와 사람이 판단합니다.
- 원문이 길면 run.py는 앞 15만 자만 사용합니다 (리포트에 표시됨).

## 만든 과정
설계 → 독립 리뷰 → 수정 → 재검토 흐름, 타임라인, 결정 기록: [docs/process.md](docs/process.md)

## 다음 작업 (진행 상황)
- [x] 01 재무 노트북: Sakura Internet(3778.T), 비교 기업 IIJ(3774.T)·GMO인터넷그룹(9449.T)
- [x] 02 토론 노트북: 연습 모드로 전체 흐름과 근거 id 자동 걸러내기 확인
- [x] 코드 리뷰 10건 수정 + 회귀 테스트: [docs/code_review_2026-11-23.md](docs/code_review_2026-11-23.md)
- [x] 섹터 플레이북 분리 (generic, neocloud), 리팩터링 전후 3778.T 질문·근거 동일 확인
- [x] 03 시황 노트북: 네오클라우드 밸류체인 15개 종목
- [x] 최종 구조 리뷰 반영 (계산 오류 3건, 01 질문 → 02 토론 연결): [docs/code_review_2026-11-25.md](docs/code_review_2026-11-25.md). 남은 항목도 같은 문서에
- [x] 논증 지도: 주장 → 근거 ← 반박, 걸러진 반박, 심판 판정, 다루지 않은 경고를 한 장의 그림으로 (GitHub에서 바로 보임)
- [ ] `.env`에 API 키나 `LLM_BACKEND=claude-code`를 넣고 02 노트북을 실제 Claude로 실행 (지금 저장된 출력은 연습 모드 결과)
- [ ] 02 노트북의 예시 관점·논거를 **본인 문장으로** 교체 (지원서 투자 아이디어 제출물이 됨)
- [ ] 사쿠라인터넷 유가증권보고서·실적발표 원문을 `inputs/3778/`에 넣고 run.py 실행 → `F` 팩트를 토론 근거에 추가
- [ ] 영문 원문(실적발표 transcript, 20-F 등) 한 건 포함 → 영어 원문 소화 역량 증거
- [x] 두 번째 섹터 플레이북: 반도체 (지표는 재고일수 하나만 추가, SK하이닉스·TSMC·도쿄일렉트론 데이터로 규칙 확인)
- [ ] 리서치 에이전트 루프: 공시 검색·원문 읽기를 도구로, 찾은 팩트는 인용 검증 후에만 근거에 추가
- [ ] 평가 세트: 하네스를 바꿀 때 인용 정확도·심판 점수가 나아졌는지 측정
- [ ] 한국 종목용 DART API 연동 (Yahoo 대신 공시 원문 숫자)
