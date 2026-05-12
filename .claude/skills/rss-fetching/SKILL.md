---
name: rss-fetching
description: RSS·Atom 피드 30개 내외를 PAR:10으로 fetch하고 RawItem 스키마로 정규화. 자동 발견.
---

## 절차

1. `data/rss_sources.yaml` 로드 → `enabled: true`인 항목만
2. `httpx.AsyncClient` + `asyncio.gather`로 PAR:10 fetch (개별 timeout 8초)
3. `feedparser`로 파싱 → `entries` 순회
4. HTML 태그 제거 (`BeautifulSoup(text, "html.parser").get_text()`)
5. URL 정규화: `utm_*`, `ref_*`, `fbclid`, `gclid` 등 파라미터 제거 + fragment 제거
6. `published_at` 없으면 `fetched_at`으로 대체
7. `category`·`language`·`weight`는 source에서 상속

## 실패 처리

- HTTPError·timeout: warning + 빈 리스트 반환
- 파싱 error: warning + 빈 리스트
- 전체 source의 ≥50% 실패 시 caller가 abort

## 산출물

`outputs/daily/YYYY-MM-DD/01_raw.jsonl`

## 동시성·rate limit

- PAR:10 (asyncio.Semaphore)
- 단일 도메인 동시 요청 ≤2 (도메인별 sub-semaphore)
- 전체 timeout 90초 — 초과 시 abort

## 구현

- 코드: `scripts/fetch_rss.py`
- 진입점: `fetch_all(sources) -> (items, failed_source_names)`
- CLI 검증: `python -m scripts.fetch_rss --validate`
- 임계 상수: `scripts/state_store.py` (`RSS_FETCH_PAR`, `RSS_FETCH_TIMEOUT_SEC`,
  `RSS_DOMAIN_CONCURRENCY`, `RSS_FAILURE_ABORT_RATIO`)
