---
description: YAML 파일 편집 규칙
paths: ["**/*.yaml", "**/*.yml"]
---

YAML 파일을 편집할 때 다음 규칙을 따른다.

## 들여쓰기·문자열

- 들여쓰기: 스페이스 2칸 (탭 금지)
- 문자열은 큰따옴표 권장. 특수문자나 콜론 `:`을 포함하면 필수
- 한국어 문자열도 큰따옴표로 감싸기

## enum 필드

- `category`, `language` 같은 enum 필드는 허용값을 같은 줄 또는 위 줄에
  주석으로 1줄 표시
  ```yaml
  # category: ai_general | ai_for_cfo | ai_company_earnings | ai_kr_companies | ai_finance_jobs
  category: "ai_general"
  ```

## 검증

- 편집 후 hook이 자동으로 `python -c "import yaml,sys; yaml.safe_load(...)"` 검증
- `data/rss_sources.yaml` 편집 후에는 `/add-source` 또는
  `python -m scripts.fetch_rss --validate`로 fetch 가능성도 확인

## 금지 사항

- YAML 앵커·alias (`&`, `*`) 사용 금지 — 가독성 우선
- 멀티라인 스칼라(`|`, `>`)는 system prompt나 long description에만
- 빈 값은 명시적으로 `null` 또는 빈 문자열 `""`
