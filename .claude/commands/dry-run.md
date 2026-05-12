---
description: 발송 없이 미리보기 (PR 검토용)
allowed-tools: Read, Bash, WebSearch
---

```bash
python -m scripts.main --dry-run
```

실행 후 `outputs/daily/$(date +%Y-%m-%d)/04_slack_blocks.json`을 사람이
읽기 쉬운 텍스트로 렌더링해 출력:

- 메시지 헤더(날짜·요일)
- 각 그룹별 항목 목록 (rank · title_ko · one_liner_ko · why_it_matters · url)
- 총 blocks 수와 메시지 분할 여부

Slack API 호출은 하지 않음. `sent_history.jsonl`·`last_message.json` 미변경.
