"""Tests for the Gridie digest path: config lookup, schema, prompt routing,
block renderer, and main.py --digest=gridie wiring."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import scripts.main as main_mod
from scripts.build_blocks import (
    build_messages_for,
    build_messages_gridie,
    format_date_header,
)
from scripts.curate import (
    EMIT_DIGEST_TOOL_CFO,
    EMIT_DIGEST_TOOL_GRIDIE,
    _tool_for,
    build_system,
    curate,
)
from scripts.state_store import (
    DIGESTS,
    GRIDIE_CURATED_SCHEMA,
    GRIDIE_MIN_ITEMS,
    GRIDIE_TARGET_ITEMS,
    get_digest,
    validate_curated_with_schema,
)

# ---------------------------------------------------------------------------
# Config lookup
# ---------------------------------------------------------------------------


def test_digests_registry_has_cfo_and_gridie():
    assert "cfo" in DIGESTS
    assert "gridie" in DIGESTS


def test_get_digest_returns_correct_config():
    cfo = get_digest("cfo")
    assert cfo.id == "cfo"
    assert cfo.target_items == 10
    assert cfo.min_items == 8
    assert cfo.prompt_file == "curate.md"

    gridie = get_digest("gridie")
    assert gridie.id == "gridie"
    assert gridie.target_items == GRIDIE_TARGET_ITEMS == 7
    assert gridie.min_items == GRIDIE_MIN_ITEMS == 5
    assert gridie.prompt_file == "curate_gridie.md"
    assert gridie.header_emoji == "🤖"
    assert gridie.header_label == "Gridie AI Trend"


def test_get_digest_rejects_unknown():
    with pytest.raises(ValueError, match="unknown digest"):
        get_digest("nope")


def test_cfo_and_gridie_have_distinct_state_files():
    cfo = get_digest("cfo")
    gridie = get_digest("gridie")
    assert cfo.sent_history_file != gridie.sent_history_file
    assert cfo.last_message_file != gridie.last_message_file


# ---------------------------------------------------------------------------
# Gridie schema
# ---------------------------------------------------------------------------


def _gridie_item(rank: int, **overrides) -> dict:
    item = {
        "rank": rank,
        "source_category": "ai_general",
        "title_ko": f"OpenAI, 신규 기능 {rank} 출시",
        "summary_ko": "OpenAI가 이번 주 새로운 기능을 발표하며 개발자 워크플로 변화 가능성을 시사했습니다.",
        "gridie_perspective": "범용 모델보다 운영형 도구가 일상이 되는 흐름을 보여줍니다.",
        "source_name": "OpenAI",
        "url": f"https://openai.com/blog/feature-{rank}",
        "confidence": 0.88,
    }
    item.update(overrides)
    return item


def _gridie_payload(n: int = GRIDIE_TARGET_ITEMS) -> dict:
    return {"items": [_gridie_item(i) for i in range(1, n + 1)]}


def test_gridie_schema_accepts_valid_payload():
    ok, err = validate_curated_with_schema(_gridie_payload(), GRIDIE_CURATED_SCHEMA)
    assert ok, err


def test_gridie_schema_accepts_min_items():
    ok, err = validate_curated_with_schema(_gridie_payload(GRIDIE_MIN_ITEMS), GRIDIE_CURATED_SCHEMA)
    assert ok, err


def test_gridie_schema_rejects_too_few_items():
    payload = _gridie_payload(GRIDIE_MIN_ITEMS - 1)
    ok, _ = validate_curated_with_schema(payload, GRIDIE_CURATED_SCHEMA)
    assert not ok


def test_gridie_schema_rejects_too_many_items():
    payload = _gridie_payload(GRIDIE_TARGET_ITEMS + 1)
    ok, _ = validate_curated_with_schema(payload, GRIDIE_CURATED_SCHEMA)
    assert not ok


def test_gridie_schema_rejects_oversized_summary():
    payload = _gridie_payload()
    payload["items"][0]["summary_ko"] = "가" * 151  # max 150
    ok, _ = validate_curated_with_schema(payload, GRIDIE_CURATED_SCHEMA)
    assert not ok


def test_gridie_schema_rejects_oversized_perspective():
    payload = _gridie_payload()
    payload["items"][0]["gridie_perspective"] = "가" * 121  # max 120
    ok, _ = validate_curated_with_schema(payload, GRIDIE_CURATED_SCHEMA)
    assert not ok


def test_gridie_schema_rejects_missing_source_name():
    payload = _gridie_payload()
    del payload["items"][0]["source_name"]
    ok, _ = validate_curated_with_schema(payload, GRIDIE_CURATED_SCHEMA)
    assert not ok


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------


def test_tool_for_routes_cfo_and_gridie():
    cfo = get_digest("cfo")
    gridie = get_digest("gridie")
    assert _tool_for(cfo) is EMIT_DIGEST_TOOL_CFO
    assert _tool_for(gridie) is EMIT_DIGEST_TOOL_GRIDIE


def test_gridie_tool_required_fields_differ_from_cfo():
    cfo_req = set(EMIT_DIGEST_TOOL_CFO["input_schema"]["properties"]["items"]["items"]["required"])
    gridie_req = set(
        EMIT_DIGEST_TOOL_GRIDIE["input_schema"]["properties"]["items"]["items"]["required"]
    )
    assert "group" in cfo_req and "group" not in gridie_req
    assert "why_it_matters" in cfo_req and "why_it_matters" not in gridie_req
    assert "summary_ko" in gridie_req and "summary_ko" not in cfo_req
    assert "gridie_perspective" in gridie_req and "gridie_perspective" not in cfo_req


def test_build_system_loads_gridie_prompt_when_config_passed():
    gridie = get_digest("gridie")
    blocks = build_system(config=gridie)
    text = blocks[-1]["text"]
    assert "Gridie" in text
    assert "그리디" in text
    # Should NOT contain the CFO-specific phrasing
    assert "CFO·재무 임원을 위한 AI 뉴스 큐레이터" not in text


def test_build_system_defaults_to_cfo_prompt_when_no_config():
    blocks = build_system()
    assert "CFO" in blocks[-1]["text"]


# ---------------------------------------------------------------------------
# Gridie block renderer
# ---------------------------------------------------------------------------


SAMPLE_GRIDIE_ITEMS = [_gridie_item(i) for i in range(1, 6)]


def test_format_date_header_uses_gridie_emoji_when_configured():
    gridie = get_digest("gridie")
    out = format_date_header("2026-05-13", config=gridie)
    assert out.startswith("🤖 Gridie AI Trend — 2026.05.13")


def test_build_messages_gridie_structure():
    msgs = build_messages_gridie(SAMPLE_GRIDIE_ITEMS, "2026-05-13")
    assert len(msgs) == 1
    blocks = msgs[0]
    assert blocks[0]["type"] == "header"
    assert "Gridie" in blocks[0]["text"]["text"]
    # Footer at end
    assert blocks[-2]["type"] == "divider"
    assert blocks[-1]["type"] == "context"


def test_build_messages_gridie_one_section_per_item():
    msgs = build_messages_gridie(SAMPLE_GRIDIE_ITEMS, "2026-05-13")
    blocks = msgs[0]
    sections = [b for b in blocks if b.get("type") == "section"]
    # Each item = 1 section block (no separate header per item)
    assert len(sections) == len(SAMPLE_GRIDIE_ITEMS)


def test_build_messages_gridie_includes_all_bullets():
    msgs = build_messages_gridie(SAMPLE_GRIDIE_ITEMS[:1], "2026-05-13")
    flat = json.dumps(msgs, ensure_ascii=False)
    assert "• 요약:" in flat
    assert "• Gridie 관점:" in flat
    assert "• 참고:" in flat


def test_build_messages_gridie_no_group_headers():
    msgs = build_messages_gridie(SAMPLE_GRIDIE_ITEMS, "2026-05-13")
    blocks = msgs[0]
    # No section starts with the ▎ marker (which is CFO group prefix)
    for b in blocks:
        if b.get("type") == "section":
            assert not b["text"]["text"].startswith("▎")


def test_build_messages_gridie_sorts_by_rank():
    shuffled = list(reversed(SAMPLE_GRIDIE_ITEMS))
    msgs = build_messages_gridie(shuffled, "2026-05-13")
    flat = json.dumps(msgs, ensure_ascii=False)
    # The "*1. " line should appear before "*5. "
    assert flat.index("*1. ") < flat.index("*5. ")


def test_build_messages_for_routes_cfo_vs_gridie():
    cfo = get_digest("cfo")
    gridie = get_digest("gridie")

    # CFO renderer requires the CFO-shaped items; build minimal stub
    cfo_items = [
        {
            "rank": i,
            "group": "테스트",
            "source_category": "ai_general",
            "title_ko": f"제목 {i}",
            "title_original": None,
            "one_liner_ko": "요약",
            "why_it_matters": "이유",
            "url": f"https://x.com/{i}",
            "source": "Test",
            "confidence": 0.9,
        }
        for i in range(1, 6)
    ]
    cfo_msgs = build_messages_for(cfo, cfo_items, "2026-05-13")
    # CFO format starts with "🌅 CFO AI Daily" header
    assert "CFO AI Daily" in cfo_msgs[0][0]["text"]["text"]

    gridie_msgs = build_messages_for(gridie, SAMPLE_GRIDIE_ITEMS, "2026-05-13")
    assert "Gridie AI Trend" in gridie_msgs[0][0]["text"]["text"]


# ---------------------------------------------------------------------------
# curate() routes to Gridie config
# ---------------------------------------------------------------------------


def _fake_response_gridie(payload):
    block = SimpleNamespace(type="tool_use", name="emit_digest", input=payload)
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=300,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=[block], usage=usage, stop_reason="tool_use")


def test_curate_with_gridie_config_uses_gridie_tool_and_schema():
    gridie = get_digest("gridie")
    payload = _gridie_payload()
    client = MagicMock()
    client.messages.create.return_value = _fake_response_gridie(payload)

    result = curate(items=[], feedback=[], date_str="2026-05-13", client=client, config=gridie)
    assert result["status"] == "ok"
    assert result["payload"] == payload
    kwargs = client.messages.create.call_args.kwargs
    # Verify the gridie tool was sent
    assert kwargs["tools"] == [EMIT_DIGEST_TOOL_GRIDIE]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_digest"}
    # System prompt should be the Gridie one
    assert "Gridie" in kwargs["system"][-1]["text"]


def test_curate_default_still_uses_cfo():
    client = MagicMock()
    # Minimal CFO payload for back-compat default test
    cfo_items = [
        {
            "rank": i,
            "group": "g",
            "source_category": "ai_general",
            "title_ko": f"t{i}",
            "title_original": None,
            "one_liner_ko": "ol",
            "why_it_matters": "w",
            "url": f"https://x.com/{i}",
            "source": "s",
            "confidence": 0.9,
        }
        for i in range(1, 11)
    ]
    client.messages.create.return_value = _fake_response_gridie({"items": cfo_items})

    result = curate(items=[], feedback=[], date_str="2026-05-13", client=client)
    assert result["status"] == "ok"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["tools"] == [EMIT_DIGEST_TOOL_CFO]


# ---------------------------------------------------------------------------
# main.py --digest routing
# ---------------------------------------------------------------------------


def test_main_run_paused_works_for_gridie(tmp_path, monkeypatch):
    pause = tmp_path / "PAUSE"
    pause.write_text("", encoding="utf-8")
    monkeypatch.setattr(main_mod, "PAUSE_FILE", pause)
    monkeypatch.setattr(main_mod, "OUTPUTS_DIR", tmp_path / "outputs")
    exit_code = main_mod.run(digest="gridie")
    assert exit_code == 0


def test_main_run_unknown_digest_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "PAUSE_FILE", tmp_path / "PAUSE")
    with pytest.raises(ValueError, match="unknown digest"):
        main_mod.run(digest="nope")
