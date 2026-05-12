"""Slack chat.postMessage wrapper with idempotency + dry-run.

Idempotency rule (spec §5 [8]): if ``last_message.json``'s ``date`` equals
today's date, the send is skipped — the message was already posted.

Dry-run rule: when ``dry_run=True`` we never call the Slack API; the rendered
blocks are returned plus a fake response payload.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.state_store import (
    LAST_MESSAGE_FILE,
    LastMessage,
    read_json,
    write_json,
)

log = logging.getLogger(__name__)

# Token shape masking for logs
_TOKEN_PATTERN = re.compile(r"(xoxb-[\w-]+|sk-ant-[\w-]+)")


def _mask(s: str) -> str:
    return _TOKEN_PATTERN.sub("***", s)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


class SendOutcome:
    SENT = "sent"
    SKIPPED_ALREADY_SENT = "skipped_already_sent"
    DRY_RUN = "dry_run"


def already_sent_today(today: str, last_message_path: Path | str = LAST_MESSAGE_FILE) -> bool:
    last = read_json(last_message_path)
    if not last:
        return False
    return last.get("date") == today


def _today_str(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.date().isoformat()


def post_message(
    client: Any,
    channel: str,
    blocks: list[dict],
    text_fallback: str = "CFO AI Daily",
) -> dict:
    """Call ``chat.postMessage``. ``client`` must expose a ``chat_postMessage``
    method (compatible with ``slack_sdk.WebClient``)."""
    response = client.chat_postMessage(
        channel=channel,
        blocks=blocks,
        text=text_fallback,
        unfurl_links=False,
        unfurl_media=False,
    )
    return dict(response.data) if hasattr(response, "data") else dict(response)


def send_daily(
    messages: list[list[dict]],
    item_urls: list[str],
    *,
    channel: str | None = None,
    client: Any | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    last_message_path: Path | str = LAST_MESSAGE_FILE,
) -> tuple[str, dict | None]:
    """Send ``messages`` (one or more block lists) to Slack with idempotency.

    Returns (outcome, last_message_payload). ``last_message_payload`` is None
    when skipped or dry-run.
    """
    today = _today_str(now)

    if already_sent_today(today, last_message_path):
        log.info("already sent for %s — skipping", today)
        return SendOutcome.SKIPPED_ALREADY_SENT, None

    if dry_run:
        log.info("DRY RUN — would post %d message(s) to %s", len(messages), channel or "?")
        return SendOutcome.DRY_RUN, None

    channel = channel or os.environ.get("SLACK_CHANNEL_ID")
    if not channel:
        raise RuntimeError("SLACK_CHANNEL_ID not set and channel not provided")

    if client is None:
        from slack_sdk import WebClient

        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            raise RuntimeError("SLACK_BOT_TOKEN not set")
        client = WebClient(token=token)

    first_ts: str | None = None
    for idx, blocks in enumerate(messages):
        resp = post_message(client, channel, blocks)
        if not resp.get("ok"):
            raise RuntimeError(
                f"Slack postMessage failed (part {idx + 1}/{len(messages)}): " f"{_mask(str(resp))}"
            )
        if first_ts is None:
            first_ts = resp.get("ts")

    payload: LastMessage = LastMessage(
        date=today,
        ts=first_ts or "",
        channel=channel,
        item_urls=list(item_urls),
    )
    write_json(last_message_path, payload)
    log.info("sent %d message(s); ts=%s", len(messages), first_ts)
    return SendOutcome.SENT, payload
