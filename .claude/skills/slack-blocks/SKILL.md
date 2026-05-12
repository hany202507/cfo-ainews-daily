---
name: slack-blocks
description: CuratedItem × 10 → Slack Block Kit JSON. 그룹 헤더 + 항목 sections. 자동 발견.
---

## Block 구조

1. `header` (plain_text): `🌅 CFO AI Daily — YYYY.MM.DD (요일)`
2. 그룹별 반복 (2–3개):
   - `section` (mrkdwn): `▎*{group_name}*`
   - 각 항목 (1 항목 = 2 blocks):
     - `section` (mrkdwn): `*{rank}. {title_ko}*` + 영문일 때 `\n_{title_original}_`
       + `\n⤷ 한 줄: {one_liner_ko}\n⤷ CFO 관점: {why_it_matters}`
     - `context` (mrkdwn): `원문: <{url}|{host}> | {lang}` (lang은 EN/KO)
3. `divider`
4. `context` (footer): `👍/👎/🔥로 피드백 주세요. 내일 큐레이션에 반영됩니다.`

## 언어 표기

- `title_original`이 존재하면 EN, null이면 KO
- 별도 language 필드 없이 큐레이션 결과로 추론

## 제약

- 메시지당 blocks ≤ 50개 (Slack 한도)
- section text ≤ 3000자 (Slack 한도)
- 초과 시 그룹 경계에서 분할 → 두 메시지로 발송 (첫 메시지 헤더에 `(1/2)`)

## 멘션·링크

- 멘션 사용 안 함 (소음 방지)
- URL은 mrkdwn 링크 (`<url|text>`)
- `unfurl_links: false`, `unfurl_media: false` (미리보기 비활성)

## 구현

- 코드: `scripts/build_blocks.py`
- 진입점: `build_messages(items, date_str) -> list[list[dict]]`
- 발송: `scripts/slack_send.py`의 `send_daily(messages, item_urls, ...)`
- 임계 상수: `scripts/state_store.py` (`SLACK_BLOCKS_MAX`, `SLACK_SECTION_TEXT_MAX`)
