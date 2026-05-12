# CFO AI Daily — Agent Specification

> **Version**: 0.1 (initial draft)
> **Owner**: hany202507
> **Target runtime**: Claude Code + GitHub Actions (cron)
> **Last updated**: 2026-05-11

이 문서는 클로드코드(Claude Code)가 추가 질문 없이 1차 구현을 시작할 수 있도록 작성된 실행 명세서다. 사람이 감탄할 보고서가 아니라 AI가 실행할 사양서다.

`[가정]` 표시는 사용자가 명시하지 않아 디폴트로 채운 항목. 검토 후 수정 가능.

---

## 1. Why

### 1.1 한 줄 정의
매일 KST 07:00에 AI·CFO·재무 채용 동향을 한국어 다이제스트(총 10개 항목, 2–3개 카테고리)로 슬랙에 자동 발송하는 에이전트.

### 1.2 정량 성공 기준
| 지표 | 목표 |
|---|---|
| 발송 시각 | KST 07:00 ± 10분 |
| 항목 수 | 정확히 10개 (8개 미만이면 발송 보류) |
| 카테고리 그룹 수 | 2–3개 (LLM이 동적 그룹핑) |
| 한국어 비율 | 본문 100% (영문 제목 원어 병기 허용) |
| 중복률 | 14일 롤링 윈도우, URL 또는 제목 코사인 유사도 ≥0.85 항목 ≤5% |
| 1회 실행 비용 | $0.50 미만 |
| 1회 실행 시간 | 10분 미만 |
| 사람 개입 | 매 실행 0회 (PAUSE 스위치만) |

### 1.3 비목표 (명시적 제외)
- 실시간 속보 (일 1회 배치)
- 개인화 추천 (1인 발송, 학습은 본인 피드백만)
- 풀텍스트 번역 (제목·1줄요약·1줄해설만 한글화)
- 검색 가능한 아카이브 (Slack 자체 검색으로 충분)
- 모든 카테고리 매일 채우기 (소스 빈약 시 카테고리 생략 허용)

### 1.4 사람 개입 경계
- **매 실행**: 자동, 사람 개입 0회
- **사후 학습**: 슬랙 이모지(👍/👎/🔥) → 익일 큐레이션에 반영
- **비상 정지**: `PAUSE` 파일 존재 시 실행 스킵
- **명세 변경**: RSS 소스 추가·제거는 `data/rss_sources.yaml` 직접 편집

---

## 2. 카테고리 정의

### 2.1 소스 카테고리 (입력 분류용, 5개 고정)

| ID | 설명 |
|---|---|
| `ai_general` | AI 일반 동향, 모델 출시, 정책·규제 |
| `ai_for_cfo` | CFO·재무·회계 영역 AI 업데이트 (FP&A 자동화, 감사 AI, ERP+AI) |
| `ai_company_earnings` | 글로벌 AI 회사 실적·코멘트·주가·M&A |
| `ai_kr_companies` | 한국 기업 AI 도입·전략·발표 |
| `ai_finance_jobs` | AI가 재무 채용시장에 미친 영향 (국내·해외 해고·신규 직무·연봉) |

### 2.2 발송 그룹 (LLM이 매일 동적 작성, 2–3개)
- 그룹명은 그날 항목들의 주제에서 LLM이 작성. 예: "이번 주 어닝 시즌", "한국 대기업 AI 흐름", "재무직 채용 한파"
- 한 그룹당 항목 2개 미만이면 인접 그룹에 흡수
- 최대 3개 그룹 (스캐닝 부담 제한)

---

## 3. 폴더 트리

```
cfo-ainews-daily/
├── .github/
│   └── workflows/
│       └── daily.yml                # cron 트리거 (0 22 * * * UTC = KST 07:00)
├── CLAUDE.md                         # 프로젝트 루트 지침 (항상 로드)
├── .claude/
│   ├── agents/
│   │   ├── news-curator.md           # [LLM] 큐레이션·번역·랭킹 메인 에이전트
│   │   └── fact-checker.md           # [LLM] 환각 의심 항목 검증 (조건부 호출)
│   ├── skills/
│   │   ├── rss-fetching/
│   │   │   └── SKILL.md              # RSS 파싱·정규화·디덥 절차
│   │   ├── slack-blocks/
│   │   │   └── SKILL.md              # Slack Block Kit 포매팅 절차
│   │   └── feedback-learning/
│   │       └── SKILL.md              # 어제 reactions → 오늘 큐레이션 반영
│   ├── commands/
│   │   ├── run-daily.md              # 수동 트리거 (cron과 동일)
│   │   ├── dry-run.md                # 발송 없이 미리보기
│   │   └── add-source.md             # RSS 소스 등록·검증
│   ├── rules/
│   │   └── yaml-handling.md          # YAML 편집 룰 (paths: ["**/*.yaml"])
│   └── settings.json                 # hooks: ruff, black, pytest
├── scripts/
│   ├── state_store.py                # [CODE] 스키마(TypedDict) + JSONL I/O
│   ├── fetch_rss.py                  # [CODE] RSS 30개 PAR:10 fetch
│   ├── fetch_web_search.py           # [CODE] web_search 보충 호출
│   ├── dedupe.py                     # [CODE] URL·제목 유사도 디덥
│   ├── curate.py                     # [LLM] 큐레이션·번역·랭킹 (BATCH:1)
│   ├── fact_check.py                 # [LLM] 사실 확인 (PAR:3, 조건부)
│   ├── build_blocks.py               # [CODE] Slack Block Kit JSON 빌드
│   ├── slack_send.py                 # [CODE] Slack chat.postMessage
│   ├── slack_feedback.py             # [CODE] 어제 ts → reactions 조회
│   └── main.py                       # 오케스트레이션 (Run Order 구현)
├── prompts/
│   ├── curate.md                     # 큐레이션 시스템 + 사용자 프롬프트
│   ├── translate.md                  # 영문 → 한국어 번역 보조 (curate에 포함, 별도 호출은 미사용)
│   ├── fact_check.md                 # 환각 의심 항목 검증
│   └── group_titles.md               # 그룹명 생성 가이드
├── tests/
│   ├── test_dedupe.py
│   ├── test_state_store.py
│   ├── test_slack_blocks.py
│   ├── test_curate_schema.py
│   ├── fixtures/
│   │   ├── rss_sample.xml
│   │   ├── deduped_sample.jsonl
│   │   └── curated_sample.json
│   └── regression/
│       └── case_001_duplicate_url.json
├── data/
│   ├── rss_sources.yaml              # 30개 내외 RSS 소스 (편집 가능)
│   ├── sent_history.jsonl            # 발송 URL 14일 롤링 (자동 갱신)
│   ├── feedback.jsonl                # 이모지 피드백 누적
│   └── last_message.json             # 어제 메시지 ts (피드백 조회용)
├── outputs/
│   └── daily/
│       └── YYYY-MM-DD/
│           ├── 01_raw.jsonl          # RSS+web_search fetch 결과
│           ├── 02_deduped.jsonl      # 디덥 통과 항목
│           ├── 03_curated.json       # 최종 10개 + 그룹
│           └── 04_slack_blocks.json  # Slack 발송 페이로드
├── PAUSE                             # (선택) 존재 시 실행 스킵
├── pyproject.toml
├── .gitignore
└── README.md
```

---

## 4. 입출력 스키마

### 4.1 입력

**Trigger config** (`.github/workflows/daily.yml`)
```yaml
on:
  schedule:
    - cron: "0 22 * * *"        # UTC = KST 07:00
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: false
```

**RSSSource** (`data/rss_sources.yaml`)
```yaml
sources:
  - name: "Bloomberg Technology"
    url: "https://feeds.bloomberg.com/technology/news.rss"
    category: ai_general          # enum: ai_general | ai_for_cfo | ai_company_earnings | ai_kr_companies | ai_finance_jobs
    language: en                   # enum: ko | en
    weight: 1.5                    # 0.5–2.0, 신뢰도 가중치
    enabled: true
```

**RawItem** (정규화 후, `scripts/state_store.py`)
```python
from typing import TypedDict, Literal, Optional

Category = Literal["ai_general", "ai_for_cfo", "ai_company_earnings", "ai_kr_companies", "ai_finance_jobs"]
Lang = Literal["ko", "en"]

class RawItem(TypedDict):
    source: str           # RSSSource.name
    category: Category
    title: str            # 원문 제목
    url: str              # 원문 URL (정규화: utm_* 파라미터 제거)
    published_at: str     # ISO 8601, 없으면 fetched_at으로 대체
    summary: Optional[str]  # RSS description (HTML 제거)
    language: Lang
    fetched_at: str       # ISO 8601
    weight: float         # source weight 상속
```

**FeedbackRecord** (`data/feedback.jsonl`)
```python
class FeedbackRecord(TypedDict):
    date: str             # 발송일 YYYY-MM-DD
    item_url: str
    item_title_ko: str
    category: Category
    reaction: str         # 이모지명 (예: "+1", "fire", "-1")
    count: int            # 누적 카운트
```

### 4.2 LLM 중간 산출물

**CuratedItem** (LLM 출력, JSON Schema 검증 필수)
```python
class CuratedItem(TypedDict):
    rank: int                       # 1–10
    group: str                      # 발송 그룹명 (LLM 작성)
    source_category: Category
    title_ko: str                   # 한국어 제목 (최대 60자)
    title_original: Optional[str]   # 영문 원제 (영어 소스일 때)
    one_liner_ko: str               # 한 줄 요약 (최대 40자)
    why_it_matters: str             # CFO 관점 의미 (최대 80자)
    url: str
    source: str
    confidence: float               # 0.0–1.0
```

**JSON Schema** (런타임 검증용, `scripts/curate.py` 내장)
```json
{
  "type": "object",
  "required": ["items"],
  "properties": {
    "items": {
      "type": "array",
      "minItems": 8,
      "maxItems": 10,
      "items": {
        "type": "object",
        "required": ["rank","group","source_category","title_ko","one_liner_ko","why_it_matters","url","source","confidence"],
        "properties": {
          "rank":           {"type":"integer","minimum":1,"maximum":10},
          "group":          {"type":"string","minLength":1,"maxLength":40},
          "source_category":{"enum":["ai_general","ai_for_cfo","ai_company_earnings","ai_kr_companies","ai_finance_jobs"]},
          "title_ko":       {"type":"string","minLength":1,"maxLength":60},
          "title_original": {"type":["string","null"]},
          "one_liner_ko":   {"type":"string","minLength":1,"maxLength":40},
          "why_it_matters": {"type":"string","minLength":1,"maxLength":80},
          "url":            {"type":"string","format":"uri"},
          "source":         {"type":"string"},
          "confidence":     {"type":"number","minimum":0,"maximum":1}
        }
      }
    }
  }
}
```

### 4.3 최종 출력 (Slack 메시지)

```
🌅 CFO AI Daily — 2026.05.12 (월)

▎이번 주 어닝 시즌 핵심
*1. NVIDIA Q4 데이터센터 매출 47% YoY ↑* (NVIDIA Q4 Data Center Revenue Up 47% YoY)
   ⤷ 한 줄: 가이던스 상회, AI 인프라 capex 가속
   ⤷ CFO 관점: 캐파 회계 인식 시점·감가상각 주기 재검토 신호
   원문: bloomberg.com | EN

*2. ...*

▎한국 대기업 AI 도입 흐름
*5. ...*

—
👍/👎/🔥로 피드백 주세요. 내일 큐레이션에 반영됩니다.
```

---

## 5. 처리 단계 (Run Order)

각 단계: `[주체](실행모드)` + 리소스 참조.

```
[0] [CODE](SEQ) 입력 검증 + PAUSE 확인
    → main.py
    → PAUSE 파일 존재 → 로그+종료(exit 0)
    → secrets 누락 → 로그+종료(exit 1)

[1] [CODE](SEQ) 어제 메시지 reactions 조회 → feedback.jsonl append
    → scripts/slack_feedback.py → skill: feedback-learning
    → last_message.json 없으면 스킵 (첫 실행)
    → Slack reactions.get 실패 시 warning, 진행 계속

[2] [CODE](PAR:10) RSS 30개 fetch
    → scripts/fetch_rss.py → skill: rss-fetching
    → 항목당 timeout 8초, 전체 timeout 90초
    → 절반 이상 실패 시 abort (sent_history 미갱신)
    → 출력: 01_raw.jsonl (300–800건 예상)

[3] [CODE](SEQ) 디덥
    → scripts/dedupe.py
    → 단계 A: URL 정규화 → sent_history.jsonl 비교(14일) → 제거
    → 단계 B: 잔여 항목간 제목 코사인 유사도 ≥0.85 → 가중치 높은 쪽 유지
    → 단계 C: 24시간 초과 항목 제거 (published_at 기준)
    → 출력: 02_deduped.jsonl (50–150건 예상)

[4] [LLM](BATCH:1) 큐레이션·번역·그룹핑·랭킹
    → agent: news-curator
    → prompts/curate.md
    → 입력: 02_deduped.jsonl + feedback.jsonl 최근 7일
    → 모델: claude-sonnet-4-6
    → 출력: 03_curated.json (CuratedItem × 10)
    → JSON Schema 검증 → 실패 시 1회 재시도 → 2회 실패 시:
        - 검증 통과한 항목만으로 진행 (8개 이상이면)
        - 8개 미만이면 발송 보류 + 알림

[5] [LLM](PAR:3) 사실확인 (조건부)
    → agent: fact-checker
    → 트리거: confidence < 0.7 인 항목만
    → 각 항목 WebSearch로 원문·핵심 수치 재확인
    → verdict가 "remove"인 항목 제거 → 부족분은 [4] 후보군에서 보충(차순위)
    → 출력: 03_curated.json 갱신

[6] [CODE](SEQ) Slack Blocks 빌드
    → scripts/build_blocks.py → skill: slack-blocks
    → 03_curated.json → 04_slack_blocks.json
    → blocks 50개 초과 시 두 메시지로 분할

[7] [HUMAN] 게이트 — 자동 모드이므로 통상 스킵
    → PAUSE 파일이 유일한 게이트 (자세히는 §7)

[8] [CODE](SEQ) Slack 발송
    → scripts/slack_send.py
    → dry_run=true면 콘솔 출력만, postMessage 호출 X
    → 멱등성: last_message.json의 date == today면 발송 스킵(이미 발송함)
    → 응답 ts → last_message.json 덮어쓰기
    → 발송 항목 URL들 → sent_history.jsonl append

[9] [CODE](SEQ) 정리·영속화
    → sent_history.jsonl 14일 초과 prune
    → outputs/daily/ 30일 초과 폴더 삭제
    → git add data/ && git commit -m "daily: YYYY-MM-DD" && git push
```

### 5.1 PAR/BATCH 합치기·실패 처리

**[2] PAR:10 (RSS fetch)**
- 순서 보존: X (source별 후처리 정렬)
- 부분 실패: 개별 피드 실패 → warning + 진행. **전체의 50% 초과 실패 시 main abort.**
- 중복 키: (source, url) 단일 키로 source 내부 dedup
- 멱등성: 동일 실행일 재실행 → outputs/daily/YYYY-MM-DD/01_raw.jsonl 존재하면 reuse (force 옵션 시 무시)
- Rate limit: 자체 RSS는 무관. 단일 도메인 동시 요청 ≤2.

**[4] BATCH:1 (LLM 큐레이션)**
- 한 호출에 묶는 이유: 카테고리 그룹핑·랭킹·중복제거에 전역 정보 필요
- 입력 토큰 추정: 50–150건 × 평균 250토큰 ≈ 12–40K (Sonnet 4.6 200K 컨텍스트 충분)
- 토큰 초과 시 fallback: 카테고리별 BATCH:5로 후보군 1차 압축 → 최종 reduce 호출 1회
- 합치기: 단일 호출이므로 reduce 불요
- 부분 실패: JSON Schema 검증 실패 → 재시도 1회 → 2차 실패 시 부분 발송 또는 발송 보류 룰(§5 [4])
- 멱등성: 입력 02_deduped.jsonl이 같으면 LLM 출력 분산은 허용 (랭킹 미세 차이 OK)

**[5] PAR:3 (fact-check)**
- 순서 보존: 항목 단위 독립 (병합 시 원래 rank 유지)
- 부분 실패: 검증 실패한 항목만 보충, 전체 실패 X
- Rate limit: WebSearch 분당 60회 가정, 동시 3개로 충분
- 중복 키: item.url

---

## 6. CLAUDE.md (실제 본문, 클로드코드가 매 세션 로드)

````markdown
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
````

---

## 7. Human-in-the-loop 게이트

명세상 [HUMAN] 게이트는 **PAUSE 파일 하나**다. 매일 자동 발송 흐름에서 사람 검토 게이트를 두면 마찰로 며칠 만에 폐기될 위험이 크다는 사용자 결정에 따른다.

### 7.1 PAUSE 스위치

| 항목 | 내용 |
|---|---|
| 검토할 산출물 위치·형식 | repo 루트의 `PAUSE` 파일 존재 여부 (내용 무관) |
| 결정 선택지 | 존재 = 스킵 / 부재 = 진행 (이진) |
| 승인·해제 방법 | `touch PAUSE && git push` (정지) / `rm PAUSE && git push` (재개). 슬랙 발송 메시지에 "오늘 안 받기" 안내 1줄 포함 가능 |
| 반려·타임아웃 동작 | 타임아웃 없음. cron이 매일 새로 평가. PAUSE 떠 있는 한 매일 스킵 (sent_history·feedback 미변경) |

### 7.2 조건부 자동 정지 (게이트 아닌 안전망)

다음 조건은 **사람 결재 없이 자동으로 발송 보류**한다. Slack DM으로 사유만 알림.

| 조건 | 동작 |
|---|---|
| 큐레이션 후 항목 수 < 8 | 발송 보류, sent_history 미갱신 |
| fact-check에서 50% 이상 "remove" 판정 | 발송 보류 |
| Slack API 401/403 | 발송 보류 + DM 시도 (DM도 실패하면 GitHub Actions 로그에만) |
| LLM 호출 비용 추정 > $2 (단일 실행) | 발송 보류 |

이 룰들의 임계값 변경은 명세서를 수정해야 한다 (코드 안에 매직넘버 금지, `scripts/state_store.py`에 상수로).

---

## 8. Subagent 정의

### 8.1 `.claude/agents/news-curator.md`

```markdown
---
name: news-curator
description: AI 뉴스 50–150건을 받아 CFO·재무 임원용 한국어 다이제스트 10개로 큐레이션·번역·그룹핑·랭킹. 매 실행 1회 호출.
tools: Read, Write, Bash, WebSearch
model: sonnet
---

너는 CFO·재무 임원을 위한 AI 뉴스 큐레이터다. 독자는 15년차 회계사 출신 B2B SaaS 창업자(엑셀 AI 그리디)이며, 그리디의 GTM을 위해 AI 트렌드를 업무에 접목하는 관점에서 정보를 본다.

## 큐레이션 우선순위 (가중치 순)
1. **임팩트** — CFO 의사결정(인사/예산/감사/투자/규제)에 영향
2. **신선도** — 24h 내 > 일주일 내. 일주일 초과는 제외
3. **고유성** — 동일 사건의 다른 보도는 최권위 1개만 (Bloomberg > Reuters > 일반)
4. **카테고리 다양성** — 한 카테고리 5개 초과 X, 한국 카테고리 최소 2개 (가능한 경우)
5. **피드백 학습** — 어제 👍/🔥 받은 항목과 유사 패턴 가산, 👎는 감산

## 출력 규칙
- 정확히 10개 (8개 미만이면 caller가 발송 보류)
- `title_ko`: 한국어 60자 이내. 영문 제목은 `title_original`에 원어 병기.
- `one_liner_ko`: 40자 이내, 사실 위주(주관 형용사 금지: "놀라운", "충격적"  X)
- `why_it_matters`: 80자 이내, CFO·재무 관점의 액션 가능한 한 줄
- `group`: 2–3개의 동적 그룹명. 단순 카테고리명이 아니라 그날의 주제 ("이번 주 어닝 시즌", "한국 대기업 AI 흐름", "재무직 채용 한파")
- `confidence`: 보수적으로. 원문에 인용된 수치·인명·일자가 의심스럽거나 출처가 약하면 <0.7

## Do
- M&A·어닝·인사·규제·자금조달처럼 액션 가능한 정보 우선
- 영문 기사도 한국어로 본문 작성, 원어 제목은 병기
- 그리디·엑셀 AI 맥락에서 관련 있는 항목 가산 (단, 자사 PR 금지)

## Don't
- LinkedIn 셀프홍보·단순 의견글·미확인 루머
- 같은 회사·같은 사건 중복 (1개로 통합)
- 한 항목에 여러 뉴스 묶기 (1 item = 1 news)
- 형용사·부사 남발 ("매우 중요한", "엄청난")

## 입출력
- 입력: JSONL (RawItem) + 피드백 컨텍스트
- 출력: JSON `{"items": [CuratedItem × 10]}` — JSON Schema 통과 필수
- 프롬프트 본문: `prompts/curate.md`
```

### 8.2 `.claude/agents/fact-checker.md`

```markdown
---
name: fact-checker
description: confidence < 0.7 인 CuratedItem 사실 확인. WebSearch로 원문·수치·일자 재확인. write 권한 없음.
tools: WebSearch, Read
model: sonnet
---

너는 사실확인자다. CuratedItem 하나를 받아 원문 URL과 web_search 결과로 사실관계를 검증한다.

## 검증 체크리스트
1. 회사명·인물명·제품명이 실제 존재하는가
2. 인용된 수치(매출, 직원수, 투자액, 주가)가 원문에 존재하고 일치하는가
3. 발표일·이벤트일이 원문과 일치하는가 (특히 "오늘", "이번 주" 같은 상대 표현)
4. 한국어 번역이 원문 의미를 왜곡하지 않는가

## 출력 (JSON)
{
  "verdict": "pass" | "remove" | "fix",
  "reason": "검증 근거 1–2문장",
  "corrected": null 또는 CuratedItem (verdict=fix일 때만)
}

## 정책
- 의심스러우면 "remove". "fix"는 명백한 번역 오류일 때만.
- 권한: WebSearch + Read. Write 권한 없음 (수정안은 caller가 적용).
```

---

## 9. Skills

### 9.1 `.claude/skills/rss-fetching/SKILL.md`

```markdown
---
name: rss-fetching
description: RSS·Atom 피드 30개 내외를 PAR:10으로 fetch하고 RawItem 스키마로 정규화. 자동 발견.
---

## 절차
1. `data/rss_sources.yaml` 로드 → `enabled: true`인 항목만
2. `asyncio.gather`로 PAR:10 fetch (개별 timeout 8초)
3. `feedparser`로 파싱 → `entries` 순회
4. HTML 태그 제거 (`BeautifulSoup(text, "html.parser").get_text()`)
5. URL 정규화: `utm_*`, `ref_*`, `fbclid` 등 파라미터 제거
6. published_at 없으면 fetched_at으로 대체
7. category·language·weight는 source에서 상속

## 실패 처리
- HTTPError·timeout: warning + 빈 리스트 반환
- 파싱 error: warning + 빈 리스트
- 전체 source의 ≥50% 실패 시 caller가 abort

## 산출물
`outputs/daily/YYYY-MM-DD/01_raw.jsonl`

## 동시성·rate limit
- PAR:10 (asyncio semaphore)
- 단일 도메인 동시 요청 ≤2 (도메인별 sub-semaphore)
```

### 9.2 `.claude/skills/slack-blocks/SKILL.md`

```markdown
---
name: slack-blocks
description: CuratedItem × 10 → Slack Block Kit JSON. 그룹 헤더 + 항목 sections. 자동 발견.
---

## Block 구조
1. `header`: `🌅 CFO AI Daily — YYYY.MM.DD (요일)`
2. 그룹별 반복:
   - `section` (mrkdwn): `▎*{group_name}*`
   - 각 항목:
     - `section`: `*{rank}. {title_ko}*` + (영문일 때 `\n_{title_original}_`)
     - 본문 mrkdwn: `⤷ 한 줄: {one_liner_ko}\n⤷ CFO 관점: {why_it_matters}`
     - `context`: `원문: <{url}|{host}> | {lang}`
3. `divider`
4. `context` (footer): `👍/👎/🔥로 피드백 주세요. 내일 큐레이션에 반영됩니다.`

## 제약
- blocks 총 ≤50개 (Slack 한도)
- section text ≤3000자 (Slack 한도)
- 초과 시 두 메시지로 분할 (첫 메시지에 `(1/2)` 표시)

## 멘션·링크
- 멘션 사용 안 함 (소음 방지)
- URL은 mrkdwn 링크 (`<url|text>`)
```

### 9.3 `.claude/skills/feedback-learning/SKILL.md`

```markdown
---
name: feedback-learning
description: 어제 슬랙 메시지의 reactions를 조회해 feedback.jsonl에 누적하고 큐레이터에 학습 컨텍스트를 제공한다.
---

## 절차
1. `data/last_message.json` 로드 → ts·channel·item_url_list 추출
2. 없으면 (첫 실행) → 스킵
3. Slack `reactions.get(channel, timestamp=ts)` 호출
4. 응답 `message.reactions[]` 각 항목 (`name`, `count`, `users`) → 항목별 매핑
   - 어제 메시지 본문의 rank 순서로 item_url 매칭 (last_message.json의 list 사용)
5. FeedbackRecord 생성 → `feedback.jsonl` append

## 학습 신호 스코어링
- `+1` (👍) → score +1
- `fire` (🔥) → score +2
- `-1` (👎) → score -2
- 그 외 이모지 → 무시 (또는 향후 확장)

## 큐레이터 주입
`feedback.jsonl` 최근 7일 → 큐레이터 프롬프트의 `<feedback>` 섹션:
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
```

---

## 10. Commands

### 10.1 `.claude/commands/run-daily.md`

```markdown
---
description: 일일 다이제스트 실행 (GitHub Actions cron과 동일 경로)
argument-hint: [--dry-run]
allowed-tools: Read, Write, Bash, WebSearch
---

`scripts/main.py`를 실행한다. 인자 `--dry-run`이 있으면 04_slack_blocks.json까지만 만들고 Slack 발송은 하지 않는다.

실행 후: `outputs/daily/$(date +%Y-%m-%d)/` 폴더 산출물 4개 요약을 채팅창에 출력.
```

### 10.2 `.claude/commands/dry-run.md`

```markdown
---
description: 발송 없이 미리보기 (PR 검토용)
allowed-tools: Read, Bash, WebSearch
---

`python -m scripts.main --dry-run`을 실행하고, 생성된 `04_slack_blocks.json`을 사람이 읽기 쉬운 텍스트로 렌더링해 출력.
```

### 10.3 `.claude/commands/add-source.md`

```markdown
---
description: RSS 소스 추가·검증
argument-hint: <url> <category> [name] [language=en|ko] [weight=1.0]
allowed-tools: Read, Write, Bash
---

1. URL을 `data/rss_sources.yaml`에 추가 (중복 체크)
2. `python -m scripts.fetch_rss --validate --source <url>` 실행 → 파싱 가능한지 확인
3. 첫 5개 entry 제목을 출력해 사용자가 카테고리 적합성 확인
```

---

## 11. Hooks (`.claude/settings.json`)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "matchOn": ["*.py"],
        "command": "ruff check --fix $CLAUDE_FILE_PATH && black $CLAUDE_FILE_PATH"
      },
      {
        "matcher": "Write|Edit",
        "matchOn": ["*.yaml", "*.yml"],
        "command": "python -c \"import yaml,sys; yaml.safe_load(open(sys.argv[1]))\" $CLAUDE_FILE_PATH"
      }
    ],
    "Stop": [
      {
        "command": "pytest tests/ -q --tb=short --no-header"
      }
    ]
  }
}
```

원칙: 결정론적 검증(린터·포매터·스키마 검증)은 hooks. LLM에게 시키지 않는다.

---

## 12. Rules (Lazy-loaded)

### `.claude/rules/yaml-handling.md`
```markdown
---
description: YAML 파일 편집 규칙
paths: ["**/*.yaml", "**/*.yml"]
---

- 들여쓰기: 스페이스 2칸 (탭 금지)
- 문자열은 큰따옴표 권장 (특수문자·: 포함 시 필수)
- enum 필드는 허용값을 주석으로 1줄 표시
- 편집 후 hook이 자동으로 `yaml.safe_load` 검증
```

---

## 13. 도구·MCP·권한·트리거

### 13.1 도구
- `feedparser` — RSS 파싱
- `slack-sdk` — Slack Web API
- `anthropic` — Claude SDK
- `scikit-learn` — 코사인 유사도 디덥
- `pyyaml`, `beautifulsoup4`, `pydantic` (또는 TypedDict + jsonschema)
- `pytest`, `ruff`, `black` — 개발

### 13.2 MCP
사용 안 함. 외부 의존을 최소화. (필요 시 향후 Notion MCP를 brain dump 연동에 추가 가능)

### 13.3 권한 범위
- 네트워크: RSS 도메인 (whitelist X, blacklist만), Slack API (`slack.com`), Anthropic API (`api.anthropic.com`)
- 파일: repo 내 `data/`, `outputs/`만 write. `scripts/`, `prompts/`, `.claude/`는 코드 작성 시에만 write.
- Subagent별 권한: news-curator(Read/Write/Bash/WebSearch), fact-checker(Read/WebSearch만)

### 13.4 트리거
- **Scheduled**: GitHub Actions `cron: "0 22 * * *"` (UTC = KST 07:00)
- **Manual**: `workflow_dispatch` (Actions UI에서 버튼)
- **Slash**: `/run-daily`, `/dry-run`, `/add-source` (Claude Code 내)
- **File change**: 없음
- **Event**: 없음 (Slack reactions는 폴링)

### 13.5 비밀
| Secret | 용도 | 등록 위치 |
|---|---|---|
| `SLACK_BOT_TOKEN` | Slack chat.postMessage + reactions.get | GitHub Secrets |
| `SLACK_CHANNEL_ID` | 발송 채널 | GitHub Secrets |
| `ANTHROPIC_API_KEY` | Claude SDK | GitHub Secrets |

`.github/workflows/daily.yml`의 `env:`로 주입. 코드에서 `os.environ`으로만 접근. 로그 출력 시 토큰 패턴(`xoxb-*`, `sk-ant-*`) 정규식 마스킹.

---

## 14. Build Order (1회, 구현 순서)

1. `mkdir -p` 폴더 트리 + 빈 파일 `touch`
2. `pyproject.toml` + 의존성 설치 (`pip install -e .`)
3. `CLAUDE.md` 초안 (§6 본문)
4. **스키마 우선**: `scripts/state_store.py`에 TypedDict + JSONL I/O + 상수(임계값) + JSON Schema
5. **[CODE] 코어** + 단위 테스트
   - `fetch_rss.py` (+ test fixtures: `tests/fixtures/rss_sample.xml`)
   - `dedupe.py` (URL 정규화, 코사인 유사도)
   - `build_blocks.py` (Block Kit 변환)
   - `slack_send.py` + `slack_feedback.py` (slack-sdk mock으로 테스트)
6. **[LLM] 호출부**
   - `prompts/curate.md` 작성
   - `scripts/curate.py` (Anthropic SDK + JSON Schema 검증 + 1회 재시도)
   - `scripts/fact_check.py` (conditional 호출)
7. Subagent (`news-curator.md`, `fact-checker.md`) + Skill (3개) + Command (3개) + Rule (yaml-handling) 파일 작성 — frontmatter 표준 준수
8. PAUSE 게이트 + 조건부 발송 보류 룰 구현 (§7.2)
9. `scripts/main.py` 오케스트레이션 — Run Order(§5) 그대로 코드 구조에 반영
10. PAR/BATCH 합치기·부분 실패 처리 구현 (§5.1)
11. 통합 테스트 (§15) — 3개 정상 + 1개 게이트 + 1개 병렬 일관성
12. `.claude/settings.json` hooks 설정
13. `.github/workflows/daily.yml` 작성 + GitHub Secrets 매핑
14. **첫 검증 흐름**: `workflow_dispatch`로 `dry_run=true` 1회 → 결과 점검 → `dry_run=false` 1회 → 슬랙 수신 확인 → cron 활성화

---

## 15. 테스트 케이스

### Test 1 (정상): 실적 시즌
- **입력**: `tests/fixtures/rss_sample.xml` 30개 source, 어닝 8건 + ai_general 5건 + 한국기업 4건 (총 ~80건 entries)
- **기대**:
  - `03_curated.json` 정확히 10개
  - "어닝 시즌 핵심" 또는 유사 그룹에 4–5개
  - 모든 항목 한국어, 모든 `confidence ≥ 0.7`
- **검증**: JSON Schema 통과 + Block Kit blocks 수 ≤50

### Test 2 (부족): 항목 6개만
- **입력**: dedupe 후 가용 6건
- **기대**: web_search 보충 시도 → 10개 채우면 정상 발송 / 못 채우면 발송 보류 + warning 로그
- **검증**: 발송 보류 시 `last_message.json`·`sent_history.jsonl` 미갱신

### Test 3 (회귀): 중복
- **입력**: 어제 발송 URL이 오늘 RSS에 재등장 + 유사 제목의 다른 URL (코사인 0.91)
- **기대**: dedupe 단계에서 두 항목 모두 제거. 동일 사건의 다른 보도면 가장 권위있는 1개만 통과
- **검증**: `02_deduped.jsonl`에 해당 URL 부재

### Test 4 ([HUMAN] 게이트): PAUSE
- **입력**: repo 루트에 `PAUSE` 파일 존재 + 정상 RSS
- **기대**: `[0]` 단계에서 즉시 종료, Slack API 호출 0회, sent_history 변경 X
- **검증**: 로그에 `"PAUSED"` 출력 + exit code 0 + Slack mock의 call count == 0

### Test 5 (병렬 일관성): PAR:10 vs SEQ
- **입력**: 동일 30개 RSS feed
- **기대**: PAR:10 결과를 (source, url)로 정렬했을 때 SEQ 결과와 항목 집합 100% 일치
- **검증**: `assert set((i["source"], i["url"]) for i in par_out) == set((i["source"], i["url"]) for i in seq_out)`

---

## 16. 운영 메모

### 16.1 RSS 소스 초기 목록 `[가정]`
사용자가 별도 명시 안 했으므로 디폴트로 다음을 제안 (`data/rss_sources.yaml` 초기 시드). 운영하며 추가·교체.

- **ai_general**: TechCrunch AI, The Verge AI, Hacker News (front), MIT Tech Review AI
- **ai_for_cfo**: CFO Dive, CFO.com, McKinsey Digital, BCG Insights (AI 태그)
- **ai_company_earnings**: Bloomberg Technology, Reuters Tech, Seeking Alpha (티커별 NVDA·MSFT·GOOGL·META·AMZN), The Information(무료)
- **ai_kr_companies**: 한경 IT, 조선비즈 테크, 매일경제 기업, 디일렉, 바이라인네트워크
- **ai_finance_jobs**: efinancialcareers, eFC RSS, 잡코리아 채용동향, LinkedIn News (Finance)

총 ~30개. 첫 1주 운영 후 항목 수·품질로 조정.

### 16.2 비용 추정 `[가정]`
- Sonnet 4.6 입력 토큰 30K + 출력 4K × 1회 큐레이션 ≈ $0.15–0.20
- fact-check 평균 3건 × 입력 2K + 출력 0.5K ≈ $0.05
- 합계 일 $0.20–0.30 × 30일 ≈ **월 $6–10**
- GitHub Actions: private repo 무료 한도 내(<10분/일)

### 16.3 자체 점검 (제출 전)

- [x] 표준 폴더 트리 + 각 파일 1줄 역할 (§3)
- [x] CLAUDE.md 200–300줄 범위 (§6, 약 180줄)
- [x] 모든 처리 단계 `[LLM]/[CODE]/[HUMAN]` + `(SEQ)/(PAR:N)/(BATCH:N)` 태그 (§5)
- [x] Skill/Subagent/Command/Hook 결정 규칙대로 분류, frontmatter 표준 (§8–11)
- [x] [HUMAN] 게이트 4개 항목 명시 (§7.1)
- [x] PAR/BATCH 동시성·rate limit·합치기·부분실패 (§5.1)
- [x] 입출력 스키마 코드 가능 (TypedDict + JSON Schema, §4)
- [x] Build Order + Run Order 둘 다 (§14, §5)
- [x] 테스트 3개 + [HUMAN] 시나리오 + 병렬 일관성 (§15)
- [x] 클로드코드가 이 문서만으로 1차 구현 가능

---

## 17. 1차 구현 후 검토 항목 (오픈 이슈)

운영 1–2주 후 사용자가 결정할 것:

1. **그룹명 스타일** — 동적 그룹명이 너무 자유분방하면 카테고리 고정으로 회귀
2. **카테고리 가중치** — 한국 vs 글로벌 비율, 채용 카테고리 빈도
3. **fact-check 트리거 임계값** — 0.7이 너무 높거나 낮으면 조정
4. **소스 화이트리스트** — 품질 낮은 source 제거, 추가 소스 발굴
5. **이모지 학습 가중치** — 현재 +1/+2/-2가 적정한지
6. **그리디 GTM 메시지와의 연결** — 현재 명세에는 미반영. 향후 `gtm_messages.yaml`(blog-agent와 공유) 참조하여 큐레이션 시 적합도 가산 가능
