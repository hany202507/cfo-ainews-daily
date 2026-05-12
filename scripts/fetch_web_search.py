"""Web-search supplement when RSS-derived candidate count is too low.

Triggered by `main.py` only if dedupe leaves fewer than TARGET_ITEMS candidates
(spec Test 2 scenario). Uses Anthropic web_search server tool to surface
recent CFO/AI/finance-jobs items and returns them as RawItems.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from scripts.curate import estimate_cost_usd
from scripts.fact_check import WEB_SEARCH_TOOL
from scripts.fetch_rss import normalize_url
from scripts.state_store import CATEGORIES, CURATE_MODEL, RawItem

log = logging.getLogger(__name__)


SUBMIT_CANDIDATES_TOOL: dict[str, Any] = {
    "name": "submit_candidates",
    "description": ("Submit web-search-derived candidate items as RawItems. " "Call exactly once."),
    "input_schema": {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "source",
                        "category",
                        "title",
                        "url",
                        "published_at",
                        "language",
                    ],
                    "properties": {
                        "source": {"type": "string"},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "published_at": {
                            "type": "string",
                            "description": "ISO 8601 timestamp, UTC preferred",
                        },
                        "summary": {"type": ["string", "null"]},
                        "language": {"type": "string", "enum": ["ko", "en"]},
                    },
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    },
}


def _build_prompt(target_n: int, existing_urls: list[str], today: str) -> str:
    urls_blob = "\n".join(f"- {u}" for u in existing_urls[:30])
    return (
        f"오늘 날짜: {today}\n"
        f"필요한 추가 항목 수: 약 {target_n}개.\n"
        "주제: AI 일반 동향 / CFO·재무 AI / AI 회사 실적·M&A / "
        "한국 기업 AI 도입 / AI가 재무 채용에 미친 영향.\n"
        "24시간 이내 보도가 우선이며, 같은 사건은 가장 권위 있는 1건만.\n\n"
        f"이미 확보된 후보 URL (중복 회피):\n{urls_blob or '(없음)'}\n\n"
        "web_search로 조사하고, 최종적으로 `submit_candidates`를 호출해 결과 제출."
    )


def fetch_supplemental(
    *,
    target_n: int,
    existing_urls: list[str],
    client: Any | None = None,
    model: str = CURATE_MODEL,
    max_tokens: int = 4096,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one web-search call. Returns {"items": [RawItem], "cost_usd": float}."""
    if target_n <= 0:
        return {"items": [], "cost_usd": 0.0}

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    now = now or datetime.now(UTC)
    today = now.date().isoformat()
    fetched_at = now.isoformat()

    user_text = _build_prompt(target_n, existing_urls, today)
    messages = [{"role": "user", "content": user_text}]
    user_msg = user_text
    total_cost = 0.0
    pause_count = 0
    response = None

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            tools=[WEB_SEARCH_TOOL, SUBMIT_CANDIDATES_TOOL],
        )
        total_cost += estimate_cost_usd(getattr(response, "usage", None))
        if getattr(response, "stop_reason", None) != "pause_turn":
            break
        pause_count += 1
        if pause_count > 3:
            log.warning("fetch_supplemental: too many pause_turn resumes")
            break
        messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": response.content},
        ]

    raw_items: list[RawItem] = []
    seen = set(normalize_url(u) for u in existing_urls)
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != "submit_candidates":
            continue
        payload = getattr(block, "input", None) or {}
        for it in payload.get("items", []):
            url = normalize_url(it.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            raw_items.append(
                RawItem(
                    source=it.get("source", "web_search"),
                    category=it.get("category", "ai_general"),  # type: ignore[arg-type]
                    title=it.get("title", "").strip(),
                    url=url,
                    published_at=it.get("published_at", fetched_at),
                    summary=it.get("summary"),
                    language=it.get("language", "en"),  # type: ignore[arg-type]
                    fetched_at=fetched_at,
                    weight=1.0,
                )
            )
    log.info("fetch_supplemental: %d items, cost=$%.4f", len(raw_items), total_cost)
    return {"items": raw_items, "cost_usd": total_cost}
