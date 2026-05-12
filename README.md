# CFO AI Daily

매일 KST 07:00에 AI·CFO·재무 채용 동향을 한국어 다이제스트(10개 항목, 2–3개 카테고리)로
Slack에 자동 발송하는 에이전트.

전체 명세: [`cfo-ai-daily-spec.md`](./cfo-ai-daily-spec.md)
운영 가이드: [`CLAUDE.md`](./CLAUDE.md)

## 빠른 시작

```bash
# 1) 의존성 설치 (Python 3.12+)
pip install -e ".[dev]"

# 2) 환경변수 설정
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_CHANNEL_ID="C..."
export ANTHROPIC_API_KEY="sk-ant-..."

# 3) 발송 없이 미리보기
python -m scripts.main --dry-run

# 4) 실제 발송
python -m scripts.main
```

## 일시 정지 / 재개

```bash
# 정지 — 다음 cron부터 발송 스킵
touch PAUSE && git add PAUSE && git commit -m "pause" && git push

# 재개
rm PAUSE && git commit -am "resume" && git push
```

## 테스트 / 린트

```bash
pytest tests/ -q
ruff check . && black --check .
```

## 아키텍처

GitHub Actions cron(22:00 UTC = KST 07:00) → `scripts/main.py` →
RSS fetch(PAR:10) → dedupe → LLM curate(BATCH:1) → 조건부 fact-check(PAR:3) →
Slack 발송 → 상태 영속화(git push).

LLM 호출은 큐레이션 1회 + 조건부 사실확인 N회. 나머지는 결정론적 코드.

## 라이선스

MIT
