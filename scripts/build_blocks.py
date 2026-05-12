"""CuratedItem list → Slack Block Kit messages (spec §9.2).

``build_messages(items, date_str)`` returns one list of blocks per Slack message.
When the total block count or section text size exceeds Slack limits, the output
is split into multiple messages with a ``(N/M)`` indicator in the header.
"""

from __future__ import annotations

from datetime import date as _date_cls
from urllib.parse import urlparse

from scripts.state_store import (
    SLACK_BLOCKS_MAX,
    SLACK_SECTION_TEXT_MAX,
    CuratedItem,
)

# Korean single-char weekday: 0=Mon … 6=Sun
_WEEKDAY_KO: tuple[str, ...] = ("월", "화", "수", "목", "금", "토", "일")

FOOTER_TEXT = "👍/👎/🔥로 피드백 주세요. 내일 큐레이션에 반영됩니다."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_date_header(date_str: str, *, part: tuple[int, int] | None = None) -> str:
    """`2026-05-12` → `🌅 CFO AI Daily — 2026.05.12 (월)`. Adds `(N/M)` when split."""
    d = _date_cls.fromisoformat(date_str)
    weekday = _WEEKDAY_KO[d.weekday()]
    base = f"🌅 CFO AI Daily — {d.year}.{d.month:02d}.{d.day:02d} ({weekday})"
    if part:
        base += f" ({part[0]}/{part[1]})"
    return base


def host_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


def _lang_label(item: CuratedItem) -> str:
    """Infer language from presence of an English original title."""
    return "EN" if item.get("title_original") else "KO"


def group_items(items: list[CuratedItem]) -> list[tuple[str, list[CuratedItem]]]:
    """Group by ``group`` field, preserving first-appearance order. Sort each
    group's items by rank ascending."""
    buckets: dict[str, list[CuratedItem]] = {}
    order: list[str] = []
    for item in items:
        g = item["group"]
        if g not in buckets:
            buckets[g] = []
            order.append(g)
        buckets[g].append(item)
    return [(g, sorted(buckets[g], key=lambda x: x["rank"])) for g in order]


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------


def _header_block(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _group_block(group_name: str) -> dict:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"▎*{group_name}*"},
    }


def _item_blocks(item: CuratedItem) -> list[dict]:
    title_line = f"*{item['rank']}. {item['title_ko']}*"
    if item.get("title_original"):
        title_line += f"\n_{item['title_original']}_"

    body = (
        f"{title_line}\n"
        f"⤷ 한 줄: {item['one_liner_ko']}\n"
        f"⤷ CFO 관점: {item['why_it_matters']}"
    )
    body = body[:SLACK_SECTION_TEXT_MAX]  # hard cap per Slack limit

    host = host_from_url(item["url"])
    context_text = f"원문: <{item['url']}|{host}> | {_lang_label(item)}"

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]},
    ]


def _footer_blocks() -> list[dict]:
    return [
        {"type": "divider"},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": FOOTER_TEXT}],
        },
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_blocks(items: list[CuratedItem], date_str: str) -> list[dict]:
    """Build a single message's blocks. May exceed Slack limits — caller should
    use ``build_messages`` for splitting."""
    blocks: list[dict] = [_header_block(format_date_header(date_str))]
    for group_name, group_items_list in group_items(items):
        blocks.append(_group_block(group_name))
        for it in group_items_list:
            blocks.extend(_item_blocks(it))
    blocks.extend(_footer_blocks())
    return blocks


def build_messages(items: list[CuratedItem], date_str: str) -> list[list[dict]]:
    """Return one or more block lists; splits when total > SLACK_BLOCKS_MAX.

    Split strategy: split at group boundaries so an item is never orphaned.
    Each part gets its own header with `(N/M)` indicator.
    """
    grouped = group_items(items)
    # build per-group block sequences
    per_group_blocks: list[list[dict]] = []
    for group_name, group_items_list in grouped:
        blocks = [_group_block(group_name)]
        for it in group_items_list:
            blocks.extend(_item_blocks(it))
        per_group_blocks.append(blocks)

    footer = _footer_blocks()
    # reserve 1 header + len(footer) for each message
    overhead = 1 + len(footer)
    body_budget = SLACK_BLOCKS_MAX - overhead

    # Pack groups into messages greedy-style
    parts: list[list[list[dict]]] = [[]]  # list of messages, each a list of group-block-lists
    current_size = 0
    for group_blocks in per_group_blocks:
        if current_size + len(group_blocks) > body_budget and parts[-1]:
            parts.append([])
            current_size = 0
        parts[-1].append(group_blocks)
        current_size += len(group_blocks)

    total = len(parts)
    messages: list[list[dict]] = []
    for idx, part_groups in enumerate(parts, start=1):
        header_text = format_date_header(date_str, part=(idx, total) if total > 1 else None)
        msg: list[dict] = [_header_block(header_text)]
        for gblocks in part_groups:
            msg.extend(gblocks)
        msg.extend(footer)
        messages.append(msg)
    return messages
