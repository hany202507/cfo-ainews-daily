"""Tests for slack_feedback — reactions fetch, fan-out, end-to-end collect."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from scripts.slack_feedback import (
    collect_yesterday_feedback,
    fetch_reactions,
    reactions_to_records,
)
from scripts.state_store import read_jsonl, write_json


def _ok_reactions_response(reactions: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.data = {"ok": True, "message": {"reactions": reactions}}
    return resp


def _not_ok_response(error: str = "message_not_found") -> MagicMock:
    resp = MagicMock()
    resp.data = {"ok": False, "error": error}
    return resp


# ---------------------------------------------------------------------------
# reactions_to_records (fan-out)
# ---------------------------------------------------------------------------


def test_reactions_to_records_basic_fanout():
    reactions = [
        {"name": "+1", "count": 2, "users": ["U1", "U2"]},
        {"name": "fire", "count": 1, "users": ["U3"]},
    ]
    items = [
        {"url": "https://a.com/1", "title_ko": "기사 1", "category": "ai_general"},
        {"url": "https://a.com/2", "title_ko": "기사 2", "category": "ai_company_earnings"},
    ]
    records = reactions_to_records(reactions, items, "2026-05-11")
    # 2 reactions × 2 items = 4 records
    assert len(records) == 4
    by_url_and_reaction = {(r["item_url"], r["reaction"]): r for r in records}
    assert by_url_and_reaction[("https://a.com/1", "+1")]["count"] == 2
    assert by_url_and_reaction[("https://a.com/2", "fire")]["count"] == 1


def test_reactions_to_records_skips_zero_count():
    reactions = [{"name": "-1", "count": 0, "users": []}]
    items = [{"url": "https://a.com/1", "title_ko": "x", "category": "ai_general"}]
    assert reactions_to_records(reactions, items, "2026-05-11") == []


def test_reactions_to_records_handles_no_items():
    reactions = [{"name": "+1", "count": 1}]
    assert reactions_to_records(reactions, [], "2026-05-11") == []


def test_reactions_to_records_preserves_metadata():
    reactions = [{"name": "fire", "count": 3}]
    items = [{"url": "https://a.com/1", "title_ko": "Korean Title", "category": "ai_kr_companies"}]
    records = reactions_to_records(reactions, items, "2026-05-11")
    assert records[0]["item_title_ko"] == "Korean Title"
    assert records[0]["category"] == "ai_kr_companies"
    assert records[0]["date"] == "2026-05-11"


# ---------------------------------------------------------------------------
# fetch_reactions
# ---------------------------------------------------------------------------


def test_fetch_reactions_returns_list_on_ok():
    client = MagicMock()
    client.reactions_get.return_value = _ok_reactions_response([{"name": "fire", "count": 1}])
    out = fetch_reactions(client, "C123", "111.1")
    assert out == [{"name": "fire", "count": 1}]


def test_fetch_reactions_empty_on_not_ok():
    client = MagicMock()
    client.reactions_get.return_value = _not_ok_response()
    assert fetch_reactions(client, "C123", "111.1") == []


def test_fetch_reactions_empty_on_exception():
    client = MagicMock()
    client.reactions_get.side_effect = RuntimeError("network")
    assert fetch_reactions(client, "C123", "111.1") == []


def test_fetch_reactions_handles_message_without_reactions_key():
    client = MagicMock()
    client.reactions_get.return_value = _ok_reactions_response([])  # empty list
    assert fetch_reactions(client, "C123", "111.1") == []


# ---------------------------------------------------------------------------
# collect_yesterday_feedback
# ---------------------------------------------------------------------------


def test_collect_returns_zero_when_no_last_message(tmp_path: Path):
    out = collect_yesterday_feedback(
        client=MagicMock(),
        last_message_path=tmp_path / "missing.json",
        feedback_path=tmp_path / "feedback.jsonl",
    )
    assert out == 0


def test_collect_returns_zero_when_last_message_missing_ts(tmp_path: Path):
    lm_path = tmp_path / "lm.json"
    write_json(lm_path, {"date": "2026-05-11", "ts": "", "channel": "C123", "item_urls": []})
    out = collect_yesterday_feedback(
        client=MagicMock(),
        last_message_path=lm_path,
        feedback_path=tmp_path / "feedback.jsonl",
    )
    assert out == 0


def test_collect_appends_feedback_records(tmp_path: Path):
    lm_path = tmp_path / "lm.json"
    fb_path = tmp_path / "feedback.jsonl"
    write_json(
        lm_path,
        {
            "date": "2026-05-11",
            "ts": "111.1",
            "channel": "C123",
            "item_urls": ["https://a.com/1", "https://a.com/2"],
        },
    )
    client = MagicMock()
    client.reactions_get.return_value = _ok_reactions_response(
        [{"name": "fire", "count": 2}, {"name": "+1", "count": 1}]
    )
    item_meta = [
        {"url": "https://a.com/1", "title_ko": "기사 1", "category": "ai_general"},
        {"url": "https://a.com/2", "title_ko": "기사 2", "category": "ai_for_cfo"},
    ]
    n = collect_yesterday_feedback(
        client=client,
        last_message_path=lm_path,
        feedback_path=fb_path,
        item_meta=item_meta,
    )
    assert n == 4  # 2 reactions × 2 items
    records = list(read_jsonl(fb_path))
    reactions_seen = {r["reaction"] for r in records}
    assert reactions_seen == {"fire", "+1"}


def test_collect_falls_back_to_url_only_meta(tmp_path: Path):
    lm_path = tmp_path / "lm.json"
    fb_path = tmp_path / "feedback.jsonl"
    write_json(
        lm_path,
        {
            "date": "2026-05-11",
            "ts": "111.1",
            "channel": "C123",
            "item_urls": ["https://a.com/1"],
        },
    )
    client = MagicMock()
    client.reactions_get.return_value = _ok_reactions_response([{"name": "fire", "count": 1}])
    n = collect_yesterday_feedback(
        client=client,
        last_message_path=lm_path,
        feedback_path=fb_path,
        item_meta=None,  # force fallback
    )
    assert n == 1
    records = list(read_jsonl(fb_path))
    assert records[0]["item_url"] == "https://a.com/1"
    assert records[0]["item_title_ko"] == ""
