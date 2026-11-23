# Asia small/mid-cap research assistant

공시·실적발표 원문(.txt)을 넣으면 **검증된 팩트 → 투자 논거/반대 논거/촉매 초안 → 회의적 검토** 순서로 리서치 노트 초안을 만드는 LLM 파이프라인입니다.
최종 투자 판단은 사람이 합니다. 이 도구는 초안과 점검표를 만들 뿐이고, 가격·밸류에이션·추천은 포함하지 않습니다.

## 구조
| 단계 | 하는 일 | 자동 점검 (코드) |
|---|---|---|
| 1. extract | 원문에서 사실만 추출, 원문 그대로의 인용문 첨부 | 인용문이 원문에 실제로 있는지 확인, 없으면 폐기 |
| 2. thesis | 시장의 믿음 vs 가능한 오해, 논거·반대 논거·촉매 (모든 주장에 팩트 id 인용) | 인용한 팩트 id가 존재하는지 확인 |
| 3. verify | 별도 호출로 초안을 의심하며 검토 (supported / overreach / unsupported) | - |

## 사용법
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # 키를 코드에 쓰지 마세요
# inputs/3778/ 에 실적발표·공시 텍스트를 .txt로 저장
python run.py --company "Sakura Internet" --ticker 3778 --dir inputs/3778
python run.py --company "Sakura Internet" --ticker 3778 --dir inputs/3778 --view "내 가설 한 문장"
python run.py --dry-run --company Test --ticker 0000 --dir inputs/demo   # API 없이 흐름만 테스트 (inputs/demo에 아무 .txt)
```
결과는 `outputs/<티커>_<날짜>.md` (읽기용)와 `.json` (데이터)로 저장됩니다.

## 한계
- 원문이 길면 앞 15만 자만 사용합니다 (리포트에 표시됨).
- 숫자는 모델이 읽은 값이므로, 사용 전 원문과 대조해야 합니다. 인용문 검증은 "그 문장이 원문에 있다"까지만 보장합니다.
- 스크래핑·DART API 연동은 아직 없습니다. 입력은 직접 붙여넣은 텍스트입니다.
