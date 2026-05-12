# CFO AI Daily

매일 KST 07:00에 AI·CFO·재무 채용 동향을 한국어 다이제스트(10개)로 슬랙에 자동 발송하는 에이전트.

## 명령어

| 목적 | 명령어 |
|---|---|
| 일일 워크플로우 실행 | `python -m scripts.main` |
| 발송 없이 미리보기 | `python -m scripts.main --dry-run` |
| 단위 테스트 | `pytest tests/ -q` |
| 린터·포매터 | `ruff check . && black .` |
| RSS 소스 검증 | `python -m scripts.fetch_rss --validate` |
| 슬래시 커맨드 | `/run-daily`, `/dry-run`, `/add-source` |

## 아키텍처 (한 그림)

```
GitHub Actions cron (22:00 UTC)
        ↓
    scripts/main.py
        ↓
[feedback fetch] → [RSS PAR:10] → [dedupe] → [LLM curate BATCH:1] → [fact-check PAR:3]
                                                          ↓
                                                  [Slack send]
                                                          ↓
                                                  [state persist + git push]
```

LLM 호출은 큐레이션 1회 + 조건부 사실확인 N회. 나머지는 결정론적 코드.

## 디렉토리 맵
- `.claude/agents/` — 서브에이전트 (news-curator, fact-checker)
- `.claude/skills/` — 도메인 절차 (rss-fetching, slack-blocks, feedback-learning)
- `.claude/commands/` — 슬래시 커맨드
- `scripts/` — 결정론적 로직 (Python)
- `prompts/` — LLM 프롬프트 템플릿
- `data/` — 입력(rss_sources.yaml) + 상태(sent_history, feedback, last_message)
- `outputs/daily/YYYY-MM-DD/` — 단계별 산출물 (재실행 캐시)

## 도메인 용어
- **소스 카테고리**: RSS feed가 분류된 5개 enum
- **발송 그룹**: LLM이 매일 동적 작성하는 2–3개 상위 그룹
- **CuratedItem**: 최종 발송 단위 (스키마는 `scripts/state_store.py`)
- **Confidence**: LLM 자기 평가 0.0–1.0. <0.7이면 fact-check 트리거
- **Feedback Loop**: 어제 슬랙 메시지의 reactions → 오늘 큐레이션 프롬프트에 주입

## Skills · Subagents · Commands 인덱스
- Skills: `rss-fetching`, `slack-blocks`, `feedback-learning`
- Subagents: `news-curator` (main), `fact-checker` (conditional)
- Commands: `/run-daily`, `/dry-run`, `/add-source`

## 보안·금지 사항
- 비밀(`SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `SLACK_CHANNEL_ID`)은 환경변수로만 접근. 코드·로그·커밋에 평문 금지.
- 외부 발송(Slack)은 `dry_run=False`일 때만. 모든 테스트는 `dry_run=True` 강제.
- 한 실행에 LLM 호출 ≤15회 (cost guardrail). 초과 시 abort.
- `sent_history.jsonl`의 URL은 절대 재발송 금지.
- `outputs/`, `data/` 외 경로에는 쓰지 않는다.

## 다른 룰 참조
- YAML 파일 편집 규칙: `.claude/rules/yaml-handling.md` (paths 매칭 시 자동 로드)
