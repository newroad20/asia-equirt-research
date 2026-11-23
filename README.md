# Asia equity research: 재무제표 → 질문 → 논거/반대 논거 토론

아시아 네오클라우드(GPU 클라우드) 기업을 대상으로 **재무제표를 읽고, 숫자가 던지는 질문을 찾고, 그 근거를 들고 AI PM과 투자 논거·반대 논거를 토론**하는 리서치 워크플로입니다.
최종 투자 판단은 사람이 합니다. 이 도구는 근거 정리와 토론 상대 역할만 하며, 가격·밸류에이션·추천은 포함하지 않습니다.

## 구성
| 파일 | 하는 일 | 코드로 하는 자동 검증 |
|---|---|---|
| [notebooks/01_fundamentals.ipynb](notebooks/01_fundamentals.ipynb) | 연간 재무제표 수집(yfinance) → 17개 지표 → 차트 → 숫자가 던지는 질문 → 비교 기업 표 | 지표마다 근거 id(`GM_FY2026` 등) 부여 |
| [notebooks/02_debate.ipynb](notebooks/02_debate.ipynb) | 내가 BULL, AI가 BEAR PM으로 토론 → 독립 심판이 채점 → 토론 전후 관점 기록 저장 | 근거 id가 없거나 틀린 반박은 자동으로 걸러냄. 내가 인용한 id와 심판이 인용한 id도 검사 |
| [run.py](run.py) | 공시·실적발표 원문(.txt) → 팩트 추출 → 논거 초안 → 회의적 검토 | 인용문이 원문에 실제로 있는지 확인. 02 토론에서 `F1`, `F2` … 근거로 재사용 |
| [research/](research/) | 위 기능을 담은 공용 모듈 (financials, debate, llm, net) | |
| [prompts/](prompts/) | 단계별 프롬프트 (01~03 run.py, 04 BEAR PM, 05 심판) | |
| [tools/build_notebooks.py](tools/build_notebooks.py) | 노트북 두 개를 코드로 생성 | |

## 사용법
```bash
pip install -r requirements.txt
# API 키: 저장소 루트에 .env 파일을 만들고 ANTHROPIC_API_KEY=... 한 줄 (.env는 git에 올라가지 않음)
jupyter lab notebooks/      # 01 → 02 순서로 실행
```
- API 키가 없으면 02 노트북은 **연습 모드**(가짜 응답)로 돌아갑니다. 흐름과 자동 검증만 확인할 수 있습니다.
- 기본 모델은 `claude-opus-5-5`입니다. 환경변수 `ANTHROPIC_MODEL`로 바꿀 수 있습니다.
- 회사망처럼 HTTPS 인증서를 중간에서 바꾸는 네트워크에서는 Windows 인증서 저장소를 자동으로 함께 신뢰합니다 ([research/net.py](research/net.py)).

공시 원문 파이프라인:
```bash
python run.py --company "SAKURA internet" --ticker 3778 --dir inputs/3778          # inputs/3778/ 에 .txt
python run.py --dry-run --company Test --ticker 0000 --dir inputs/demo             # API 없이 흐름만 테스트
```

## 한계
- Yahoo 재무 데이터는 편의용입니다. 중요한 숫자는 EDINET(일본)·DART(한국)·MOPS(대만) 원문과 대조해야 합니다. 보통 4개년만 제공됩니다.
- 근거 id 검증은 "그 id가 존재한다"까지만 보장합니다. 인용한 근거가 주장을 실제로 뒷받침하는지는 심판 단계와 사람이 판단합니다.
- 원문이 길면 run.py는 앞 15만 자만 사용합니다 (리포트에 표시됨).

## 다음 작업 (진행 상황)
- [x] 01 재무 노트북: Sakura Internet(3778.T) 실행 완료, 비교 기업 IIJ(3774.T)·GMO인터넷그룹(9449.T)
- [x] 02 토론 노트북: 연습 모드로 전체 흐름과 근거 id 자동 걸러내기 확인
- [ ] `.env`에 API 키를 넣고 02 노트북을 실제 Claude로 실행 (지금 저장된 출력은 연습 모드 결과)
- [ ] 02 노트북의 예시 관점·논거를 **본인 문장으로** 교체 (지원서 투자 아이디어 제출물이 됨)
- [ ] 사쿠라인터넷 유가증권보고서·실적발표 원문을 `inputs/3778/`에 넣고 run.py 실행 → `F` 팩트를 토론 근거에 추가
- [ ] 영문 원문(실적발표 transcript, 20-F 등) 한 건 포함 → 영어 원문 소화 역량 증거
- [ ] 아시아 네오클라우드 비교군 확정 (한국·대만 후보 조사)
- [ ] 한국 종목용 DART API 연동 (Yahoo 대신 공시 원문 숫자)
- [ ] 코드 리뷰 결과 반영: [docs/code_review_2026-11-23.md](docs/code_review_2026-11-23.md) (높음 3건부터)
- [ ] 회전율 차트 라벨 여백 수정 반영: `python tools/build_notebooks.py` 후 01 노트북 재실행
