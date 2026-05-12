"""LLM curation step (spec §5 [4]).

Single Anthropic API call: forced `emit_digest` tool, system prompt cached.
On schema-validation failure, one retry; on the second failure return the
best partial payload so the caller can decide whether to send or hold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema

from scripts.state_store import (
    CATEGORIES,
    CURATE_MODEL,
    FEEDBACK_RECENT_DAYS,
    GRIDIE_MIN_ITEMS,
    GRIDIE_TARGET_ITEMS,
    MIN_ITEMS,
    TARGET_ITEMS,
    DigestConfig,
    FeedbackRecord,
    RawItem,
    get_digest,
    validate_curated_with_schema,
)


def _item_passes_schema(item: dict[str, Any], schema: dict[str, Any]) -> bool:
    try:
        jsonschema.validate(item, schema)
        return True
    except jsonschema.ValidationError:
        return False


log = logging.getLogger(__name__)

PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"

# Sonnet 4.6 pricing (per 1M tokens)
_PRICE_INPUT_PER_M: float = 3.0
_PRICE_OUTPUT_PER_M: float = 15.0
_CACHE_WRITE_MULT: float = 1.25
_CACHE_READ_MULT: float = 0.10


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


def _item_schema_cfo() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "rank",
            "group",
            "source_category",
            "title_ko",
            "title_original",
            "one_liner_ko",
            "why_it_matters",
            "url",
            "source",
            "confidence",
        ],
        "properties": {
            "rank": {"type": "integer", "description": "1–10 ranking (1 = top impact)"},
            "group": {"type": "string", "description": "Headline-style group name, ≤40 chars"},
            "source_category": {"type": "string", "enum": list(CATEGORIES)},
            "title_ko": {"type": "string", "description": "Korean title, ≤60 chars"},
            "title_original": {
                "type": ["string", "null"],
                "description": "Original English title if source is EN, else null",
            },
            "one_liner_ko": {"type": "string", "description": "One-line summary, ≤40 chars"},
            "why_it_matters": {
                "type": "string",
                "description": "CFO-perspective implication, ≤80 chars",
            },
            "url": {"type": "string", "description": "Original article URL"},
            "source": {"type": "string", "description": "Source publication name"},
            "confidence": {
                "type": "number",
                "description": "Self-rated confidence 0.0–1.0; <0.7 triggers fact-check",
            },
        },
        "additionalProperties": False,
    }


def _item_schema_gridie() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "rank",
            "source_category",
            "title_ko",
            "summary_ko",
            "gridie_perspective",
            "source_name",
            "url",
            "confidence",
        ],
        "properties": {
            "rank": {"type": "integer", "description": "1–7 ranking (1 = top relevance to Gridie)"},
            "source_category": {"type": "string", "enum": list(CATEGORIES)},
            "title_ko": {
                "type": "string",
                "description": "Headline-style Korean title, ≤80 chars (e.g. 'OpenAI, Agents SDK 보강')",
            },
            "summary_ko": {
                "type": "string",
                "description": "One-sentence factual Korean summary, ≤150 chars, '~했습니다' tone",
            },
            "gridie_perspective": {
                "type": "string",
                "description": "Founder-lens takeaway for the Gridie operator, ≤120 chars",
            },
            "source_name": {
                "type": "string",
                "description": "Reader-friendly publisher name (e.g. 'OpenAI', 'TechCrunch')",
            },
            "url": {"type": "string", "description": "Original article URL"},
            "confidence": {
                "type": "number",
                "description": "Self-rated confidence 0.0–1.0; <0.7 triggers fact-check",
            },
        },
        "additionalProperties": False,
    }


EMIT_DIGEST_TOOL_CFO: dict[str, Any] = {
    "name": "emit_digest",
    "description": (
        "Submit the final curated digest of 8–10 CFO/AI/finance-jobs items "
        "organized into 2–3 dynamic groups. Call exactly once."
    ),
    "input_schema": {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "minItems": MIN_ITEMS,
                "maxItems": TARGET_ITEMS,
                "items": _item_schema_cfo(),
            }
        },
        "additionalProperties": False,
    },
}


EMIT_DIGEST_TOOL_GRIDIE: dict[str, Any] = {
    "name": "emit_digest",
    "description": (
        "Submit the Gridie AI Trend digest of 5–7 AI product/tool/infra items "
        "for a B2B SaaS founder. No groups; flat numbered list. Call exactly once."
    ),
    "input_schema": {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "minItems": GRIDIE_MIN_ITEMS,
                "maxItems": GRIDIE_TARGET_ITEMS,
                "items": _item_schema_gridie(),
            }
        },
        "additionalProperties": False,
    },
}


# Back-compat alias — existing imports reference EMIT_DIGEST_TOOL (CFO).
EMIT_DIGEST_TOOL: dict[str, Any] = EMIT_DIGEST_TOOL_CFO

_DIGEST_TOOLS: dict[str, dict[str, Any]] = {
    "cfo": EMIT_DIGEST_TOOL_CFO,
    "gridie": EMIT_DIGEST_TOOL_GRIDIE,
}


def _tool_for(config: DigestConfig) -> dict[str, Any]:
    return _DIGEST_TOOLS[config.id]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _load_system_prompt(filename: str = "curate.md") -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def build_system(
    prompt_text: str | None = None,
    *,
    config: DigestConfig | None = None,
) -> list[dict[str, Any]]:
    """Cached system block (last block carries cache_control). If ``config`` is
    given and ``prompt_text`` is None, loads from ``config.prompt_file``."""
    if prompt_text is None:
        filename = config.prompt_file if config else "curate.md"
        prompt_text = _load_system_prompt(filename)
    return [
        {
            "type": "text",
            "text": prompt_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _recent_feedback(
    feedback: Iterable[FeedbackRecord], today: str, days: int = FEEDBACK_RECENT_DAYS
) -> tuple[list[FeedbackRecord], list[FeedbackRecord]]:
    """Return (good, bad) FeedbackRecords from the last `days` days."""
    today_d = datetime.fromisoformat(today).date()
    cutoff = today_d - timedelta(days=days)
    good: list[FeedbackRecord] = []
    bad: list[FeedbackRecord] = []
    for rec in feedback:
        try:
            d = datetime.fromisoformat(rec["date"]).date()
        except (KeyError, ValueError):
            continue
        if d < cutoff:
            continue
        reaction = rec.get("reaction", "")
        if reaction in {"+1", "thumbsup", "fire"}:
            good.append(rec)
        elif reaction in {"-1", "thumbsdown"}:
            bad.append(rec)
    return good, bad


def _format_feedback_block(feedback: Iterable[FeedbackRecord], today: str) -> str:
    good, bad = _recent_feedback(feedback, today)
    if not good and not bad:
        return ""
    lines = ["<feedback>"]
    if good:
        lines.append("GOOD (👍/🔥 받은 항목 — 비슷한 거 가산):")
        for r in good[:30]:
            lines.append(
                f"- [{r.get('category', '?')}] {r.get('item_title_ko', '')} — "
                f"{r['reaction']}×{r.get('count', 1)}"
            )
    if bad:
        lines.append("\nBAD (👎 받은 항목 — 비슷한 거 감산):")
        for r in bad[:30]:
            lines.append(
                f"- [{r.get('category', '?')}] {r.get('item_title_ko', '')} — "
                f"{r['reaction']}×{r.get('count', 1)}"
            )
    lines.append("</feedback>")
    return "\n".join(lines)


def build_user(
    date_str: str,
    items: list[RawItem],
    feedback: Iterable[FeedbackRecord],
    *,
    config: DigestConfig | None = None,
) -> str:
    """User-turn content: date, feedback (if any), candidate items as compact JSON."""
    cfg = config or get_digest("cfo")
    feedback_block = _format_feedback_block(feedback, date_str)
    candidates_json = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    parts = [
        f"오늘 날짜: {date_str}",
        f"후보 항목 수: {len(items)}",
        "",
    ]
    if feedback_block:
        parts.append(feedback_block)
        parts.append("")
    parts.extend(
        [
            "<candidates>",
            candidates_json,
            "</candidates>",
            "",
            f"위 후보 중 정확히 {cfg.target_items}개(부득이하면 {cfg.min_items}–{cfg.target_items - 1}개)"
            "를 골라 `emit_digest`로 제출하라.",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def estimate_cost_usd(usage: Any) -> float:
    """Compute USD cost from an Anthropic Usage object (or compatible dict).

    Sonnet 4.6 base rates: $3/$15 per 1M. Cache writes 1.25×, reads 0.1×.
    """

    def _g(name: str) -> int:
        if usage is None:
            return 0
        if isinstance(usage, dict):
            return int(usage.get(name, 0) or 0)
        return int(getattr(usage, name, 0) or 0)

    input_t = _g("input_tokens")
    output_t = _g("output_tokens")
    cache_write = _g("cache_creation_input_tokens")
    cache_read = _g("cache_read_input_tokens")
    return (
        (input_t * _PRICE_INPUT_PER_M)
        + (cache_write * _PRICE_INPUT_PER_M * _CACHE_WRITE_MULT)
        + (cache_read * _PRICE_INPUT_PER_M * _CACHE_READ_MULT)
        + (output_t * _PRICE_OUTPUT_PER_M)
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


def _extract_tool_input(response: Any) -> dict[str, Any] | None:
    """Pull `emit_digest` tool_use input from an Anthropic response."""
    if hasattr(response, "content"):
        content = response.content or []
    elif isinstance(response, dict):
        content = response.get("content", []) or []
    else:
        content = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            bname = block.get("name", "")
            payload = block.get("input")
        else:
            btype = getattr(block, "type", None)
            bname = getattr(block, "name", "")
            payload = getattr(block, "input", None)
        if btype == "tool_use" and bname == "emit_digest" and isinstance(payload, dict):
            return payload
    return None


# ---------------------------------------------------------------------------
# Curation entry point
# ---------------------------------------------------------------------------


class CurationResult(dict):
    """Lightweight result dict — typed as a dict for ease of JSON serialization."""


def curate(
    items: list[RawItem],
    feedback: list[FeedbackRecord],
    date_str: str,
    *,
    config: DigestConfig | None = None,
    client: Any | None = None,
    model: str = CURATE_MODEL,
    max_retries: int = 1,
    max_tokens: int = 8192,
) -> CurationResult:
    """Run a single curation call with one retry on schema failure.

    ``config`` selects the digest (defaults to CFO for back-compat).

    Returns:
        {
            "status": "ok" | "partial" | "failed",
            "payload": {"items": [...]} or None,
            "errors": list[str],
            "usage": list of usage dicts (one per attempt),
            "cost_usd": float (cumulative),
        }
    """
    cfg = config or get_digest("cfo")
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    system_blocks = build_system(config=cfg)
    user_text = build_user(date_str, items, feedback, config=cfg)
    messages = [{"role": "user", "content": user_text}]
    tool = _tool_for(cfg)

    errors: list[str] = []
    usages: list[dict[str, int]] = []
    total_cost = 0.0
    last_payload: dict[str, Any] | None = None

    for attempt in range(max_retries + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_digest"},
        )

        usage = getattr(response, "usage", None)
        usages.append(
            {
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "cache_creation_input_tokens": int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
                "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            }
        )
        total_cost += estimate_cost_usd(usage)

        payload = _extract_tool_input(response)
        if payload is None:
            errors.append(f"attempt {attempt + 1}: no emit_digest tool_use in response")
            log.warning(errors[-1])
            continue

        last_payload = payload
        ok, err = validate_curated_with_schema(payload, cfg.schema)
        if ok:
            return CurationResult(
                status="ok",
                payload=payload,
                errors=errors,
                usage=usages,
                cost_usd=total_cost,
            )
        errors.append(f"attempt {attempt + 1} schema: {err}")
        log.warning(errors[-1])

    # Schema validation failed after all retries — try a partial fallback.
    if last_payload and isinstance(last_payload.get("items"), list):
        valid_items = [
            it for it in last_payload["items"] if _item_passes_schema(it, cfg.item_schema)
        ]
        if len(valid_items) >= cfg.min_items:
            partial = {"items": valid_items[: cfg.target_items]}
            return CurationResult(
                status="partial",
                payload=partial,
                errors=errors,
                usage=usages,
                cost_usd=total_cost,
            )

    return CurationResult(
        status="failed",
        payload=None,
        errors=errors,
        usage=usages,
        cost_usd=total_cost,
    )
