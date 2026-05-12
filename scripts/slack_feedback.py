"""Slack reactions → feedback.jsonl appender (spec §9.3).

Design note (since the spec is light on this): Slack reactions attach to a
**whole message**, not individual blocks. The spec asks for per-item
FeedbackRecords. We bridge this by fan-out: each emoji reacted on yesterday's
message yields one FeedbackRecord per item in that message. The curator then
reads the recent feedback to bias today's picks.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from scripts.state_store import (
    FEEDBACK_FILE,
    LAST_MESSAGE_FILE,
    FeedbackRecord,
    append_jsonl,
    read_json,
)

log = logging.getLogger(__name__)


# Reaction → numeric weight (spec §9.3 "학습 신호 스코어링")
REACTION_WEIGHTS: dict[str, int] = {
    "+1": 1,
    "thumbsup": 1,
    "fire": 2,
    "-1": -2,
    "thumbsdown": -2,
}


def fetch_reactions(client: Any, channel: str, ts: str) -> list[dict[str, Any]]:
    """Return ``message.reactions`` list. ``client`` must expose ``reactions_get``."""
    try:
        resp = client.reactions_get(channel=channel, timestamp=ts, full=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("reactions.get failed: %r", exc)
        return []

    data = dict(resp.data) if hasattr(resp, "data") else dict(resp)
    if not data.get("ok"):
        log.warning("reactions.get not-ok: %s", data.get("error"))
        return []
    message = data.get("message") or {}
    return list(message.get("reactions") or [])


def reactions_to_records(
    reactions: list[dict[str, Any]],
    item_meta: list[dict[str, str]],
    date_str: str,
) -> list[FeedbackRecord]:
    """Fan-out: every (item, reaction) → one FeedbackRecord.

    ``item_meta`` is a list of ``{"url", "title_ko", "category"}`` dicts in the
    same order as the message rendering.
    """
    records: list[FeedbackRecord] = []
    for reaction in reactions:
        name = reaction.get("name")
        count = int(reaction.get("count", 0))
        if not name or count <= 0:
            continue
        for item in item_meta:
            records.append(
                FeedbackRecord(
                    date=date_str,
                    item_url=item["url"],
                    item_title_ko=item.get("title_ko", ""),
                    category=item.get("category", "ai_general"),  # type: ignore[typeddict-item]
                    reaction=name,
                    count=count,
                )
            )
    return records


def collect_yesterday_feedback(
    *,
    client: Any | None = None,
    last_message_path: Path | str = LAST_MESSAGE_FILE,
    feedback_path: Path | str = FEEDBACK_FILE,
    item_meta: list[dict[str, str]] | None = None,
) -> int:
    """End-to-end: read last_message.json → reactions.get → append to feedback.jsonl.

    ``item_meta`` may be passed explicitly (e.g. by the orchestrator that just
    curated). When omitted, item_meta is built from the URL list with empty
    title/category — still useful for url-level signal.

    Returns the number of FeedbackRecords appended.
    """
    last = read_json(last_message_path)
    if not last:
        log.info("no last_message — skipping feedback collection (first run?)")
        return 0

    ts = last.get("ts")
    channel = last.get("channel")
    urls = last.get("item_urls") or []
    if not ts or not channel:
        log.warning("last_message missing ts/channel — skipping")
        return 0

    if client is None:
        from slack_sdk import WebClient

        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            log.warning("SLACK_BOT_TOKEN not set — skipping feedback collection")
            return 0
        client = WebClient(token=token)

    reactions = fetch_reactions(client, channel, ts)
    if not reactions:
        log.info("no reactions found on %s/%s", channel, ts)
        return 0

    if item_meta is None:
        item_meta = [{"url": u, "title_ko": "", "category": "ai_general"} for u in urls]

    records = reactions_to_records(reactions, item_meta, last["date"])
    return append_jsonl(feedback_path, records)
