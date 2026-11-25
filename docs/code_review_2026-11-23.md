# 코드 리뷰 (2026-11-23): 2026-11-25 전부 수정 완료

3778.T 캐시 데이터와 연습 모드로 확인한 정확성 리뷰입니다. 현재 3778.T 실행 결과는 정상입니다(근거 id 64개 모두 고유, 가짜 id 반박 정상 차단). 아래 항목은 다른 종목이나 모델 출력이 달라질 때 문제가 됩니다.

## 높음
- [x] **근거 id 충돌** `research/financials.py` `evidence()`·`questions()`: id에서 결산월을 버려서(`FY2024(03)`과 `FY2024(12)`가 모두 `FY2024`), 결산월을 바꾼 회사는 id가 겹치고 값이 덮어써짐 → id에 월을 포함하거나 중복 시 오류
- [x] **심판 출력의 `evidence_ids: null`에서 오류** `research/debate.py` `judge()` → `c.get("evidence_ids") or []`
- [x] **근거 없는 심판 판정이 통과** `judge()`: `evidence_ids: []`인데 `supported`면 그대로 남음 → 빈 목록이면 `unsupported`로 강제

## 중간
- [x] 매출 행이 하나도 없으면 `questions()`에서 IndexError, `peer_table()`은 `r.iloc[-1]`이 try 밖이라 한 종목 실패가 전체 비교를 멈춤
- [x] `line_items()`에서 `dropna(subset=["revenue"])`가 평균 계산보다 먼저 실행돼서 첫해 ROE·ROIC가 항상 비고, 중간 연도가 빠지면 떨어진 두 해를 평균함
- [x] FCF 보완 계산이 '모든 해가 빈 경우'에만 적용됨 → `items["fcf"].fillna(items["cfo"] + items["capex"])`
- [x] `questions()`가 `evidence()`의 `prefix` 인자를 무시함

## 낮음
- [x] `pct_change()`가 기본으로 결측을 앞 값으로 채움 → `fill_method=None`
- [x] `round_md()`·`judge_md()`가 키를 바로 꺼내서(`x['point']` 등) 모델이 필드를 빠뜨리면 KeyError → `.get()`
- [x] `research/net.py`: Windows 신뢰 플래그를 무시하고 중간 인증서(CA 저장소)까지 루트로 넣음, 고정 경로 임시 파일을 동시에 쓰면 깨질 수 있음

## 확인 결과 정상
설비투자 부호 처리, 순차입금/EBITDA·영업현금흐름/순이익의 분모 조건, `questions()`의 NaN 비교, 1개년 데이터, 프롬프트의 중괄호 이스케이프

## 수정 확인
- `tests/test_research.py`: 위 실패 사례를 그대로 재현하는 회귀 테스트 10개, 전부 통과 (`python -m pytest tests -q`)
- 3778.T 실데이터 재실행: 근거 id 64개 모두 고유, 질문 10개, 두 노트북 오류 없이 실행
- 참고: 3778.T의 FY2023 ROE·ROIC는 여전히 비어 있음. 버그가 아니라 Yahoo의 FY2022 열에 값이 거의 없기 때문(평균 낼 전년 자기자본이 없음)
