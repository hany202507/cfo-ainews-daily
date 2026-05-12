"""Conditional fact-check step (spec §5 [5]).

For each CuratedItem with `confidence < FACT_CHECK_CONFIDENCE_THRESHOLD`, call
the Anthropic API with the `web_search` server tool and a `submit_verdict`
client tool. Run up to FACT_CHECK_PAR items in parallel using a thread pool.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from scripts.curate import estimate_cost_usd
from scripts.state_store import (
    CATEGORIES,
    FACT_CHECK_CONFIDENCE_THRESHOLD,
    FACT_CHECK_MODEL,
    FACT_CHECK_PAR,
    FACT_CHECK_REMOVE_RATIO_LIMIT,
    CuratedItem,
)

log = logging.getLogger(__name__)

PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

# Web search server tool — Anthropic-hosted, no client execution.
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
}

SUBMIT_VERDICT_TOOL: dict[str, Any] = {
    "name": "submit_verdict",
    "description": ("Submit the verification verdict for a single CuratedItem. Call exactly once."),
    "input_schema": {
        "type": "object",
        "required": ["verdict", "reason"],
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["pass", "remove", "fix"],
                "description": "pass = factually correct; remove = unverifiable or wrong; fix = minor translation error to correct",
            },
            "reason": {
                "type": "string",
                "description": "1–2 sentence Korean justification",
            },
            "corrected": {
                "type": ["object", "null"],
                "description": "Full corrected CuratedItem (only if verdict='fix'); otherwise null",
                "properties": {
                    "rank": {"type": "integer"},
                    "group": {"type": "string"},
                    "source_category": {"type": "string", "enum": list(CATEGORIES)},
                    "title_ko": {"type": "string"},
                    "title_original": {"type": ["string", "null"]},
                    "one_liner_ko": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "url": {"type": "string"},
                    "source": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}


def _load_system_prompt() -> str:
    return (PROMPTS_DIR / "fact_check.md").read_text(encoding="utf-8")


def _build_user_message(item: CuratedItem) -> str:
    item_json = json.dumps(item, ensure_ascii=False, indent=2)
    return (
        "다음 CuratedItem을 검증하라.\n"
        "필요하면 web_search로 1–2회만 보조 검색하고,\n"
        "최종적으로 `submit_verdict` 도구를 호출해 결과를 제출하라.\n\n"
        "<item>\n"
        f"{item_json}\n"
        "</item>"
    )


def _extract_verdict(response: Any) -> dict[str, Any] | None:
    content = getattr(response, "content", None) or []
    for block in content:
        btype = getattr(block, "type", None)
        bname = getattr(block, "name", None)
        if btype == "tool_use" and bname == "submit_verdict":
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload
    return None


def _check_one(
    client: Any,
    item: CuratedItem,
    *,
    model: str,
    system_prompt: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Run one fact-check call. Returns a dict with verdict, reason, corrected, usage, cost."""
    messages = [{"role": "user", "content": _build_user_message(item)}]
    user_msg = messages[0]["content"]

    total_cost = 0.0
    pause_count = 0
    response = None

    # Up to 3 pause_turn resumes; web_search server loops at most 10 internally.
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=[WEB_SEARCH_TOOL, SUBMIT_VERDICT_TOOL],
        )
        total_cost += estimate_cost_usd(getattr(response, "usage", None))

        if getattr(response, "stop_reason", None) != "pause_turn":
            break
        pause_count += 1
        if pause_count > 3:
            log.warning("fact-check %s: too many pause_turn resumes, aborting", item["url"])
            break
        messages = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": response.content},
        ]

    verdict = _extract_verdict(response)
    if verdict is None:
        log.warning("fact-check %s: no submit_verdict in response", item["url"])
        return {
            "url": item["url"],
            "verdict": "pass",  # conservative — don't drop if checker failed to respond
            "reason": "fact-checker did not emit a verdict; passing through",
            "corrected": None,
            "cost_usd": total_cost,
        }
    return {
        "url": item["url"],
        "verdict": verdict.get("verdict", "pass"),
        "reason": verdict.get("reason", ""),
        "corrected": verdict.get("corrected"),
        "cost_usd": total_cost,
    }


def fact_check_items(
    items: list[CuratedItem],
    *,
    client: Any | None = None,
    model: str = FACT_CHECK_MODEL,
    threshold: float = FACT_CHECK_CONFIDENCE_THRESHOLD,
    max_workers: int = FACT_CHECK_PAR,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Apply fact-checks to items with confidence < threshold.

    Returns:
        {
            "items": list[CuratedItem]   # kept + fixed items, original order
            "removed": list[str]          # URLs of removed items
            "verdicts": list[dict]        # per-checked-item verdict records
            "cost_usd": float
            "abort_send": bool            # True if ≥50% of checked were removed (§7.2)
        }
    """
    to_check = [it for it in items if it.get("confidence", 1.0) < threshold]
    if not to_check:
        return {
            "items": list(items),
            "removed": [],
            "verdicts": [],
            "cost_usd": 0.0,
            "abort_send": False,
        }

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    system_prompt = _load_system_prompt()

    verdicts: list[dict[str, Any]] = []
    total_cost = 0.0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _check_one,
                client,
                it,
                model=model,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
            ): it
            for it in to_check
        }
        for fut in as_completed(futures):
            result = fut.result()
            verdicts.append(result)
            total_cost += result.get("cost_usd", 0.0)

    verdict_by_url = {v["url"]: v for v in verdicts}
    removed_urls: list[str] = []
    out_items: list[CuratedItem] = []
    for it in items:
        v = verdict_by_url.get(it["url"])
        if v is None:
            out_items.append(it)
            continue
        if v["verdict"] == "remove":
            removed_urls.append(it["url"])
            continue
        if v["verdict"] == "fix" and isinstance(v.get("corrected"), dict):
            # Preserve rank from the original (fact-checker shouldn't reorder).
            fixed = {**v["corrected"], "rank": it["rank"]}
            out_items.append(fixed)  # type: ignore[arg-type]
            continue
        out_items.append(it)  # pass

    remove_ratio = len(removed_urls) / len(to_check) if to_check else 0.0
    abort_send = remove_ratio >= FACT_CHECK_REMOVE_RATIO_LIMIT

    log.info(
        "fact_check: checked=%d removed=%d fixed=%d cost=$%.4f abort=%s",
        len(to_check),
        len(removed_urls),
        sum(1 for v in verdicts if v["verdict"] == "fix"),
        total_cost,
        abort_send,
    )
    return {
        "items": out_items,
        "removed": removed_urls,
        "verdicts": verdicts,
        "cost_usd": total_cost,
        "abort_send": abort_send,
    }
