"""Tests for scripts/curate.py and scripts/fact_check.py.

Mocks the Anthropic client. Covers prompt assembly, forced-tool extraction,
schema validation, single retry, partial fallback, fact-check verdict routing,
and cost estimation.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.curate import (
    EMIT_DIGEST_TOOL,
    _format_feedback_block,
    build_system,
    build_user,
    curate,
    estimate_cost_usd,
)
from scripts.fact_check import fact_check_items

FIXTURE = Path(__file__).parent / "fixtures" / "curated_sample.json"
SAMPLE_ITEMS = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]


# ---------------------------------------------------------------------------
# Helpers: build a fake Anthropic response
# ---------------------------------------------------------------------------


def _fake_usage(input_tokens=1000, output_tokens=500, cache_read=0, cache_write=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )


def _tool_use_block(name: str, payload: dict):
    block = SimpleNamespace(type="tool_use", name=name, input=payload)
    return block


def _fake_response(
    *, payload: dict | None, name: str = "emit_digest", usage=None, stop_reason="tool_use"
):
    content = [_tool_use_block(name, payload)] if payload is not None else []
    return SimpleNamespace(
        content=content,
        usage=usage or _fake_usage(),
        stop_reason=stop_reason,
    )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_build_system_has_cache_control_on_last_block():
    blocks = build_system()
    assert isinstance(blocks, list)
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    # ensure the actual content text is included
    assert "CFO" in blocks[-1]["text"]


def test_build_user_includes_candidates_and_date():
    raw_items = [
        {"source": "X", "title": "Test", "url": "https://x.com/1", "category": "ai_general"}
    ]
    out = build_user("2026-05-12", raw_items, [])
    assert "2026-05-12" in out
    assert "Test" in out
    assert "<candidates>" in out and "</candidates>" in out


def test_build_user_omits_feedback_section_when_empty():
    out = build_user("2026-05-12", [], [])
    assert "<feedback>" not in out


def test_format_feedback_block_splits_good_and_bad():
    feedback = [
        {
            "date": "2026-05-11",
            "item_url": "https://a.com/1",
            "item_title_ko": "기사 A",
            "category": "ai_general",
            "reaction": "fire",
            "count": 2,
        },
        {
            "date": "2026-05-11",
            "item_url": "https://a.com/2",
            "item_title_ko": "기사 B",
            "category": "ai_for_cfo",
            "reaction": "-1",
            "count": 1,
        },
    ]
    block = _format_feedback_block(feedback, "2026-05-12")
    assert "GOOD" in block and "기사 A" in block
    assert "BAD" in block and "기사 B" in block


def test_format_feedback_block_drops_old_records():
    feedback = [
        {
            "date": "2026-04-01",  # >7d old
            "item_url": "https://a.com/1",
            "item_title_ko": "old",
            "category": "ai_general",
            "reaction": "fire",
            "count": 1,
        }
    ]
    block = _format_feedback_block(feedback, "2026-05-12")
    assert block == ""


# ---------------------------------------------------------------------------
# Tool definition shape
# ---------------------------------------------------------------------------


def test_emit_digest_tool_schema_shape():
    schema = EMIT_DIGEST_TOOL["input_schema"]
    assert schema["type"] == "object"
    assert "items" in schema["properties"]
    item_schema = schema["properties"]["items"]["items"]
    required = item_schema["required"]
    for field in (
        "rank",
        "group",
        "source_category",
        "title_ko",
        "one_liner_ko",
        "why_it_matters",
        "url",
        "source",
        "confidence",
    ):
        assert field in required


# ---------------------------------------------------------------------------
# curate() — success path
# ---------------------------------------------------------------------------


def test_curate_success_returns_ok():
    payload = {"items": SAMPLE_ITEMS}
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload=payload)

    result = curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    assert result["status"] == "ok"
    assert result["payload"] == payload
    assert client.messages.create.call_count == 1


def test_curate_forces_emit_digest_tool():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload={"items": SAMPLE_ITEMS})
    curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_digest"}
    assert any(t["name"] == "emit_digest" for t in kwargs["tools"])


def test_curate_passes_cache_control_on_system():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload={"items": SAMPLE_ITEMS})
    curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    kwargs = client.messages.create.call_args.kwargs
    system = kwargs["system"]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# curate() — retry on schema failure
# ---------------------------------------------------------------------------


def test_curate_retries_once_on_schema_failure():
    bad_item = {**SAMPLE_ITEMS[0], "confidence": 1.5}  # out of range
    bad_payload = {"items": [bad_item] + SAMPLE_ITEMS[1:]}
    good_payload = {"items": SAMPLE_ITEMS}

    client = MagicMock()
    client.messages.create.side_effect = [
        _fake_response(payload=bad_payload),
        _fake_response(payload=good_payload),
    ]
    result = curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    assert result["status"] == "ok"
    assert client.messages.create.call_count == 2


def test_curate_partial_fallback_when_some_items_valid():
    # Make 1 item invalid; the other 9 are valid → 9 >= MIN_ITEMS(8) → partial OK
    bad_item = {**SAMPLE_ITEMS[0], "confidence": 1.5}
    payload = {"items": [bad_item] + SAMPLE_ITEMS[1:]}
    client = MagicMock()
    # Both retries return the same bad payload
    client.messages.create.return_value = _fake_response(payload=payload)
    result = curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    assert result["status"] == "partial"
    assert len(result["payload"]["items"]) == 9
    assert client.messages.create.call_count == 2  # 1 + 1 retry


def test_curate_failed_when_too_few_items_valid():
    # All 10 items have bad confidence — no valid items survive
    bad_items = [{**it, "confidence": 1.5} for it in SAMPLE_ITEMS]
    payload = {"items": bad_items}
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload=payload)
    result = curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    assert result["status"] == "failed"
    assert result["payload"] is None


def test_curate_failed_when_no_tool_use_in_response():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload=None)
    result = curate(items=[], feedback=[], date_str="2026-05-12", client=client)
    assert result["status"] == "failed"
    assert any("no emit_digest" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def test_estimate_cost_basic_input_output():
    usage = _fake_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    # $3 + $15 per 1M
    assert estimate_cost_usd(usage) == pytest.approx(18.0)


def test_estimate_cost_with_cache_read_discount():
    usage = _fake_usage(input_tokens=0, output_tokens=0, cache_read=1_000_000)
    # $3 × 0.1 = $0.30
    assert estimate_cost_usd(usage) == pytest.approx(0.3)


def test_estimate_cost_with_cache_write_premium():
    usage = _fake_usage(input_tokens=0, output_tokens=0, cache_write=1_000_000)
    # $3 × 1.25 = $3.75
    assert estimate_cost_usd(usage) == pytest.approx(3.75)


def test_estimate_cost_handles_none():
    assert estimate_cost_usd(None) == 0.0


def test_estimate_cost_handles_dict():
    usage = {"input_tokens": 1000, "output_tokens": 500}
    # 1000 * $3 / 1M + 500 * $15 / 1M = 0.003 + 0.0075
    assert estimate_cost_usd(usage) == pytest.approx(0.0105)


# ---------------------------------------------------------------------------
# fact_check_items()
# ---------------------------------------------------------------------------


def _curated(url: str, *, confidence: float, rank: int = 1) -> dict:
    return {
        "rank": rank,
        "group": "테스트",
        "source_category": "ai_general",
        "title_ko": f"제목 {rank}",
        "title_original": None,
        "one_liner_ko": "요약",
        "why_it_matters": "이유",
        "url": url,
        "source": "Test",
        "confidence": confidence,
    }


def _verdict_response(verdict: str, *, corrected: dict | None = None, reason: str = ""):
    payload = {"verdict": verdict, "reason": reason or f"{verdict} 처리"}
    if corrected is not None:
        payload["corrected"] = corrected
    return _fake_response(payload=payload, name="submit_verdict", stop_reason="tool_use")


def test_fact_check_skips_high_confidence_items():
    items = [_curated("https://a.com/1", confidence=0.9, rank=1)]
    client = MagicMock()
    result = fact_check_items(items, client=client)
    assert result["items"] == items
    assert result["verdicts"] == []
    client.messages.create.assert_not_called()


def test_fact_check_pass_keeps_item():
    items = [_curated("https://a.com/1", confidence=0.5, rank=1)]
    client = MagicMock()
    client.messages.create.return_value = _verdict_response("pass")
    result = fact_check_items(items, client=client, max_workers=1)
    assert result["items"] == items
    assert result["removed"] == []
    assert result["abort_send"] is False


def test_fact_check_remove_drops_item():
    items = [
        _curated("https://a.com/1", confidence=0.5, rank=1),
        _curated("https://a.com/2", confidence=0.9, rank=2),
    ]
    client = MagicMock()
    client.messages.create.return_value = _verdict_response("remove", reason="단일 출처")
    result = fact_check_items(items, client=client, max_workers=1)
    assert [it["url"] for it in result["items"]] == ["https://a.com/2"]
    assert result["removed"] == ["https://a.com/1"]


def test_fact_check_fix_replaces_item_preserving_rank():
    original = _curated("https://a.com/1", confidence=0.5, rank=3)
    corrected = {**original, "title_ko": "수정된 제목", "rank": 99}  # rank should be ignored
    client = MagicMock()
    client.messages.create.return_value = _verdict_response("fix", corrected=corrected)
    result = fact_check_items([original], client=client, max_workers=1)
    assert result["items"][0]["title_ko"] == "수정된 제목"
    assert result["items"][0]["rank"] == 3  # original rank preserved


def test_fact_check_abort_when_majority_removed():
    items = [
        _curated("https://a.com/1", confidence=0.5, rank=1),
        _curated("https://a.com/2", confidence=0.5, rank=2),
        _curated("https://a.com/3", confidence=0.5, rank=3),
    ]
    client = MagicMock()
    client.messages.create.return_value = _verdict_response("remove")
    result = fact_check_items(items, client=client, max_workers=1)
    assert result["abort_send"] is True
    assert result["items"] == []


def test_fact_check_missing_verdict_passes_through():
    items = [_curated("https://a.com/1", confidence=0.5, rank=1)]
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload=None)  # no tool_use
    result = fact_check_items(items, client=client, max_workers=1)
    assert result["items"] == items
    assert result["removed"] == []
