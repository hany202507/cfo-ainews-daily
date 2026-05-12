"""Tests for build_blocks — Slack Block Kit assembly per spec §9.2."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.build_blocks import (
    FOOTER_TEXT,
    build_blocks,
    build_messages,
    format_date_header,
    group_items,
    host_from_url,
)
from scripts.state_store import SLACK_BLOCKS_MAX

FIXTURE = Path(__file__).parent / "fixtures" / "curated_sample.json"
SAMPLE = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_format_date_header_weekday_korean():
    # 2026-05-11 is a Monday
    assert format_date_header("2026-05-11") == "🌅 CFO AI Daily — 2026.05.11 (월)"
    # 2026-05-17 is a Sunday
    assert format_date_header("2026-05-17") == "🌅 CFO AI Daily — 2026.05.17 (일)"


def test_format_date_header_with_part_suffix():
    out = format_date_header("2026-05-12", part=(1, 2))
    assert out.endswith("(1/2)")


def test_host_from_url_strips_www():
    assert host_from_url("https://www.bloomberg.com/articles/abc") == "bloomberg.com"
    assert host_from_url("https://hankyung.com/path") == "hankyung.com"


def test_group_items_preserves_first_appearance_order():
    out = group_items(SAMPLE)
    names = [g for g, _ in out]
    assert names == ["이번 주 어닝 시즌", "한국 대기업 AI 흐름", "재무직 채용 변화"]


def test_group_items_sorts_by_rank_within_group():
    shuffled = list(reversed(SAMPLE))
    out = group_items(shuffled)
    for _, items in out:
        ranks = [i["rank"] for i in items]
        assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# build_blocks
# ---------------------------------------------------------------------------


def test_build_blocks_starts_with_header():
    blocks = build_blocks(SAMPLE, "2026-05-12")
    assert blocks[0]["type"] == "header"
    assert "CFO AI Daily" in blocks[0]["text"]["text"]


def test_build_blocks_ends_with_footer():
    blocks = build_blocks(SAMPLE, "2026-05-12")
    assert blocks[-2]["type"] == "divider"
    assert blocks[-1]["type"] == "context"
    assert FOOTER_TEXT in blocks[-1]["elements"][0]["text"]


def test_build_blocks_includes_all_group_headers():
    blocks = build_blocks(SAMPLE, "2026-05-12")
    mrkdwn_blocks = [
        b for b in blocks if b.get("type") == "section" and b["text"]["text"].startswith("▎")
    ]
    assert len(mrkdwn_blocks) == 3  # 3 groups in fixture


def test_build_blocks_under_slack_limit():
    blocks = build_blocks(SAMPLE, "2026-05-12")
    assert len(blocks) <= SLACK_BLOCKS_MAX


def test_build_blocks_includes_title_original_when_present():
    blocks = build_blocks(SAMPLE, "2026-05-12")
    flattened = json.dumps(blocks, ensure_ascii=False)
    assert "NVIDIA Q4 Data Center Revenue Up 47% YoY" in flattened


def test_build_blocks_omits_title_original_for_korean_only():
    # rank 4: 삼성전자 HBM4 — title_original is null
    blocks = build_blocks([s for s in SAMPLE if s["rank"] == 4], "2026-05-12")
    flattened = json.dumps(blocks, ensure_ascii=False)
    assert "삼성전자 HBM4 양산 본격화" in flattened
    # the body should not contain an italic-wrapped english original
    assert "_None_" not in flattened
    assert "_null_" not in flattened


def test_build_blocks_context_has_host_and_lang_label():
    blocks = build_blocks([s for s in SAMPLE if s["rank"] == 1], "2026-05-12")
    contexts = [b for b in blocks if b.get("type") == "context"]
    # there is the per-item context + the footer context
    item_ctx = next(b for b in contexts if "원문" in b["elements"][0]["text"])
    text = item_ctx["elements"][0]["text"]
    assert "bloomberg.com" in text
    assert "| EN" in text


def test_build_blocks_korean_only_item_labeled_ko():
    blocks = build_blocks([s for s in SAMPLE if s["rank"] == 4], "2026-05-12")
    contexts = [b for b in blocks if b.get("type") == "context"]
    item_ctx = next(b for b in contexts if "원문" in b["elements"][0]["text"])
    assert "| KO" in item_ctx["elements"][0]["text"]


def test_build_blocks_item_body_contains_one_liner_and_why():
    blocks = build_blocks([s for s in SAMPLE if s["rank"] == 1], "2026-05-12")
    flat = json.dumps(blocks, ensure_ascii=False)
    assert "⤷ 한 줄: 가이던스 상회, AI 인프라 capex 가속" in flat
    assert "⤷ CFO 관점:" in flat


# ---------------------------------------------------------------------------
# build_messages — splitting
# ---------------------------------------------------------------------------


def test_build_messages_single_when_under_limit():
    msgs = build_messages(SAMPLE, "2026-05-12")
    assert len(msgs) == 1
    assert all(len(m) <= SLACK_BLOCKS_MAX for m in msgs)


def test_build_messages_splits_when_over_limit(monkeypatch):
    """Force the per-message block budget so small that a split is required."""
    import scripts.build_blocks as bb

    monkeypatch.setattr(bb, "SLACK_BLOCKS_MAX", 12)
    msgs = bb.build_messages(SAMPLE, "2026-05-12")
    assert len(msgs) >= 2
    # Every part must have its own header and footer
    for m in msgs:
        assert m[0]["type"] == "header"
        assert m[-2]["type"] == "divider"
        assert m[-1]["type"] == "context"
    # Header should be tagged with (N/M)
    first_header_text = msgs[0][0]["text"]["text"]
    assert f"(1/{len(msgs)})" in first_header_text


def test_build_messages_split_preserves_all_items(monkeypatch):
    import scripts.build_blocks as bb

    monkeypatch.setattr(bb, "SLACK_BLOCKS_MAX", 12)
    msgs = bb.build_messages(SAMPLE, "2026-05-12")
    flat = json.dumps(msgs, ensure_ascii=False)
    # Compare by URL — title strings can contain quotes that get JSON-escaped.
    for item in SAMPLE:
        assert item["url"] in flat
