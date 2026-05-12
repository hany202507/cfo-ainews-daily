"""Tests for slack_send — dry-run, idempotency, and postMessage flow."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts.slack_send import (
    SendOutcome,
    _mask,
    already_sent_today,
    post_message,
    send_daily,
)
from scripts.state_store import read_json, write_json

NOW = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)
TODAY = NOW.date().isoformat()


def _ok_response(ts: str = "1234.5678") -> MagicMock:
    resp = MagicMock()
    resp.data = {"ok": True, "ts": ts, "channel": "C123"}
    return resp


def _err_response() -> MagicMock:
    resp = MagicMock()
    resp.data = {"ok": False, "error": "channel_not_found"}
    return resp


def test_already_sent_today_false_when_missing(tmp_path: Path):
    assert already_sent_today(TODAY, tmp_path / "lm.json") is False


def test_already_sent_today_true_when_same_date(tmp_path: Path):
    path = tmp_path / "lm.json"
    write_json(path, {"date": TODAY, "ts": "x", "channel": "C123", "item_urls": []})
    assert already_sent_today(TODAY, path) is True


def test_already_sent_today_false_when_different_date(tmp_path: Path):
    path = tmp_path / "lm.json"
    write_json(path, {"date": "2026-05-10", "ts": "x", "channel": "C123", "item_urls": []})
    assert already_sent_today(TODAY, path) is False


def test_post_message_calls_chat_postmessage():
    client = MagicMock()
    client.chat_postMessage.return_value = _ok_response()
    out = post_message(client, "C123", [{"type": "section"}])
    assert out["ok"] is True
    client.chat_postMessage.assert_called_once()
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C123"
    assert kwargs["unfurl_links"] is False
    assert kwargs["unfurl_media"] is False


def test_send_daily_dry_run_does_not_call_client(tmp_path: Path):
    client = MagicMock()
    outcome, payload = send_daily(
        messages=[[{"type": "header"}]],
        item_urls=["https://x.com/1"],
        channel="C123",
        client=client,
        dry_run=True,
        now=NOW,
        last_message_path=tmp_path / "lm.json",
    )
    assert outcome == SendOutcome.DRY_RUN
    assert payload is None
    client.chat_postMessage.assert_not_called()


def test_send_daily_skips_when_already_sent(tmp_path: Path):
    path = tmp_path / "lm.json"
    write_json(path, {"date": TODAY, "ts": "old", "channel": "C123", "item_urls": []})

    client = MagicMock()
    outcome, payload = send_daily(
        messages=[[{"type": "header"}]],
        item_urls=["https://x.com/1"],
        channel="C123",
        client=client,
        dry_run=False,
        now=NOW,
        last_message_path=path,
    )
    assert outcome == SendOutcome.SKIPPED_ALREADY_SENT
    assert payload is None
    client.chat_postMessage.assert_not_called()


def test_send_daily_posts_and_writes_last_message(tmp_path: Path):
    path = tmp_path / "lm.json"
    client = MagicMock()
    client.chat_postMessage.return_value = _ok_response(ts="9999.0001")

    outcome, payload = send_daily(
        messages=[[{"type": "header"}]],
        item_urls=["https://x.com/1", "https://x.com/2"],
        channel="C123",
        client=client,
        dry_run=False,
        now=NOW,
        last_message_path=path,
    )
    assert outcome == SendOutcome.SENT
    assert payload["ts"] == "9999.0001"
    assert payload["date"] == TODAY
    persisted = read_json(path)
    assert persisted["ts"] == "9999.0001"
    assert persisted["item_urls"] == ["https://x.com/1", "https://x.com/2"]


def test_send_daily_multi_message_keeps_first_ts(tmp_path: Path):
    client = MagicMock()
    client.chat_postMessage.side_effect = [_ok_response("111.1"), _ok_response("222.2")]
    outcome, payload = send_daily(
        messages=[[{"type": "header"}], [{"type": "header"}]],
        item_urls=["https://x.com/1"],
        channel="C123",
        client=client,
        dry_run=False,
        now=NOW,
        last_message_path=tmp_path / "lm.json",
    )
    assert outcome == SendOutcome.SENT
    assert payload["ts"] == "111.1"
    assert client.chat_postMessage.call_count == 2


def test_send_daily_raises_on_api_error(tmp_path: Path):
    client = MagicMock()
    client.chat_postMessage.return_value = _err_response()
    with pytest.raises(RuntimeError, match="postMessage failed"):
        send_daily(
            messages=[[{"type": "header"}]],
            item_urls=[],
            channel="C123",
            client=client,
            dry_run=False,
            now=NOW,
            last_message_path=tmp_path / "lm.json",
        )


def test_send_daily_raises_without_channel(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    client = MagicMock()
    with pytest.raises(RuntimeError, match="SLACK_CHANNEL_ID"):
        send_daily(
            messages=[[{"type": "header"}]],
            item_urls=[],
            channel=None,
            client=client,
            dry_run=False,
            now=NOW,
            last_message_path=tmp_path / "lm.json",
        )


def test_mask_redacts_bot_and_anthropic_tokens():
    raw = "got xoxb-abc-123 and sk-ant-xyz789 keys"
    out = _mask(raw)
    assert "xoxb-" not in out
    assert "sk-ant-" not in out
    assert "***" in out
