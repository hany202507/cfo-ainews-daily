---
name: feedback-learning
description: 어제 슬랙 메시지의 reactions를 조회해 feedback.jsonl에 누적하고 큐레이터에 학습 컨텍스트를 제공한다.
---

## 절차

1. `data/last_message.json` 로드 → `ts`·`channel`·`item_urls` 추출
2. 없으면 (첫 실행) → 스킵
3. Slack `reactions.get(channel, timestamp=ts, full=True)` 호출
4. 응답 `message.reactions[]` 각 항목 (`name`, `count`, `users`) → 항목별 매핑
   - 슬랙 반응은 메시지 단위라서 메시지 안 모든 항목에 fan-out
     (각 reaction × 각 item → 1 FeedbackRecord)
5. FeedbackRecord 생성 → `feedback.jsonl` append

## 학습 신호 스코어링

- `+1` / `thumbsup` (👍) → score +1
- `fire` (🔥) → score +2
- `-1` / `thumbsdown` (👎) → score -2
- 그 외 이모지 → 무시 (또는 향후 확장)

## 큐레이터 주입

`feedback.jsonl` 최근 7일 → 큐레이터 시스템 프롬프트의 `<feedback>` 섹션:

```
<feedback>
GOOD (👍/🔥 받은 항목 — 비슷한 거 가산):
- [ai_company_earnings] NVIDIA Q4 데이터센터 매출 47% YoY ↑ — fire×2
- ...

BAD (👎 받은 항목 — 비슷한 거 감산):
- [ai_general] OpenAI CEO 인터뷰 발췌 — thumbsdown×1
- ...
</feedback>
```

## 실패 처리

- `last_message.json` 없음 → 첫 실행으로 간주, 스킵
- Slack API 401/403/네트워크 오류 → warning만 출력, 발송 흐름 계속
- `feedback.jsonl` append 실패 → warning, 계속

## 구현

- 코드: `scripts/slack_feedback.py`
- 진입점: `collect_yesterday_feedback(client=..., item_meta=...) -> int`
- 큐레이터 프롬프트 주입: `scripts/curate.py`의 `_format_feedback_block()`
- 임계 상수: `scripts/state_store.py` (`FEEDBACK_RECENT_DAYS = 7`)
