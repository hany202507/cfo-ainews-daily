---
description: 일일 다이제스트 실행 (GitHub Actions cron과 동일 경로)
argument-hint: [--dry-run]
allowed-tools: Read, Write, Bash, WebSearch
---

`scripts/main.py`를 실행한다.

```bash
python -m scripts.main {{args}}
```

인자 `--dry-run`이 있으면 `04_slack_blocks.json`까지만 만들고 Slack 발송은
스킵한다. 인자 없으면 정상 발송 흐름 (단, `PAUSE` 파일이 있으면 즉시 종료).

실행 후 `outputs/daily/$(date +%Y-%m-%d)/` 폴더의 4개 산출물
(`01_raw.jsonl`, `02_deduped.jsonl`, `03_curated.json`,
`04_slack_blocks.json`) 각각의 항목 수를 요약해서 채팅창에 출력.

실패 시 종료 코드 1과 함께 stderr에 사유를 출력 — 정상 실행은 0.
