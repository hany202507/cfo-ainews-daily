---
description: RSS 소스 추가·검증
argument-hint: <url> <category> [name] [language=en|ko] [weight=1.0]
allowed-tools: Read, Write, Bash
---

새 RSS 소스를 `data/rss_sources.yaml`에 추가하고 fetch 가능한지 검증한다.

## 인자

- `url` (필수): RSS·Atom 피드 URL
- `category` (필수): `ai_general | ai_for_cfo | ai_company_earnings |
  ai_kr_companies | ai_finance_jobs`
- `name` (선택): 표시 이름. 생략 시 URL host로 자동 채움
- `language` (선택, 기본 `en`): `ko` 또는 `en`
- `weight` (선택, 기본 `1.0`): 0.5–2.0 신뢰도 가중치

## 절차

1. `data/rss_sources.yaml`을 읽어 URL 중복 체크 (이미 있으면 abort)
2. 새 entry append (enabled: true)
3. 검증 실행:
   ```bash
   python -m scripts.fetch_rss --validate --source <url>
   ```
4. 검증 통과 시 첫 5개 entry 제목을 출력해 카테고리 적합성 확인
5. 검증 실패 시 YAML에서 방금 추가한 entry 제거하고 에러 보고

## YAML 편집 규칙

`.claude/rules/yaml-handling.md` 룰이 자동 로드되어 들여쓰기·따옴표 규칙
적용됨.
