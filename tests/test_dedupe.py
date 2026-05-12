"""Tests for the three-stage dedupe pipeline (§5 [3])."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.dedupe import (
    cosine_dedupe,
    dedupe_all,
    filter_by_age,
    filter_by_sent_history,
    load_sent_urls,
    prune_history,
)
from scripts.state_store import RawItem, write_jsonl

NOW = datetime(2026, 5, 12, 0, 0, tzinfo=UTC)


def _item(
    title: str,
    url: str,
    *,
    published_at: datetime | None = None,
    weight: float = 1.0,
    source: str = "Test",
    category: str = "ai_general",
    language: str = "en",
) -> RawItem:
    return RawItem(
        source=source,
        category=category,  # type: ignore[arg-type]
        title=title,
        url=url,
        published_at=(published_at or NOW).isoformat(),
        summary=None,
        language=language,  # type: ignore[arg-type]
        fetched_at=NOW.isoformat(),
        weight=weight,
    )


# ---------------------------------------------------------------------------
# Stage A: sent history
# ---------------------------------------------------------------------------


def test_filter_by_sent_history_drops_exact_url():
    items = [
        _item("A", "https://x.com/a"),
        _item("B", "https://x.com/b"),
    ]
    out = filter_by_sent_history(items, {"https://x.com/a"})
    assert [i["title"] for i in out] == ["B"]


def test_filter_by_sent_history_normalizes_url():
    items = [_item("A", "https://x.com/a?utm_source=rss")]
    out = filter_by_sent_history(items, {"https://x.com/a"})
    assert out == []


def test_filter_by_sent_history_dedupes_within_batch():
    items = [
        _item("A1", "https://x.com/a"),
        _item("A2", "https://x.com/a?utm_source=rss"),
    ]
    out = filter_by_sent_history(items, set())
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Stage B: cosine
# ---------------------------------------------------------------------------


def test_cosine_dedupe_keeps_distinct_titles():
    items = [
        _item("NVIDIA Q4 earnings", "https://x.com/1"),
        _item("Samsung HBM4 production", "https://x.com/2"),
        _item("OpenAI new pricing tier", "https://x.com/3"),
    ]
    out = cosine_dedupe(items, threshold=0.85)
    assert len(out) == 3


def test_cosine_dedupe_collapses_near_duplicates_keeping_higher_weight():
    items = [
        _item("NVIDIA Q4 data center revenue up 47% YoY", "https://a.com/1", weight=1.0),
        _item("NVIDIA Q4 data center revenue up 47% YoY", "https://b.com/2", weight=2.0),
    ]
    out = cosine_dedupe(items, threshold=0.85)
    assert len(out) == 1
    assert out[0]["url"] == "https://b.com/2"
    assert out[0]["weight"] == 2.0


def test_cosine_dedupe_handles_empty_input():
    assert cosine_dedupe([], threshold=0.85) == []


def test_cosine_dedupe_handles_single_item():
    items = [_item("only", "https://x.com/1")]
    assert cosine_dedupe(items, threshold=0.85) == items


def test_cosine_dedupe_threshold_respected():
    # Same headline with a one-word delta — clearly similar, not identical
    items = [
        _item("NVIDIA Q4 data center revenue up 47% YoY", "https://x.com/1", weight=1.0),
        _item("NVIDIA Q4 data center revenue rose 47% YoY", "https://x.com/2", weight=1.0),
    ]
    out_strict = cosine_dedupe(items, threshold=0.99)
    out_loose = cosine_dedupe(items, threshold=0.5)
    assert len(out_strict) == 2
    assert len(out_loose) == 1


# ---------------------------------------------------------------------------
# Stage C: age
# ---------------------------------------------------------------------------


def test_filter_by_age_keeps_recent():
    items = [
        _item("recent", "https://x.com/1", published_at=NOW - timedelta(hours=3)),
        _item("old", "https://x.com/2", published_at=NOW - timedelta(hours=48)),
    ]
    out = filter_by_age(items, hours=24, now=NOW)
    assert [i["title"] for i in out] == ["recent"]


def test_filter_by_age_boundary_inclusive():
    items = [_item("edge", "https://x.com/1", published_at=NOW - timedelta(hours=24))]
    out = filter_by_age(items, hours=24, now=NOW)
    assert len(out) == 1


def test_filter_by_age_handles_naive_timestamp():
    items = [
        {
            **_item("naive", "https://x.com/1"),
            "published_at": "2026-05-11T22:00:00",
        }
    ]
    out = filter_by_age(items, hours=24, now=NOW)
    assert len(out) == 1


def test_filter_by_age_skips_malformed_timestamp():
    items = [{**_item("bad", "https://x.com/1"), "published_at": "not-a-date"}]
    out = filter_by_age(items, hours=24, now=NOW)
    assert out == []


# ---------------------------------------------------------------------------
# History I/O
# ---------------------------------------------------------------------------


def test_load_sent_urls_window(tmp_path: Path):
    path = tmp_path / "sent.jsonl"
    records = [
        {"date": (NOW - timedelta(days=20)).date().isoformat(), "url": "https://x.com/old"},
        {"date": (NOW - timedelta(days=2)).date().isoformat(), "url": "https://x.com/recent"},
    ]
    write_jsonl(path, records)
    urls = load_sent_urls(path, days=14, now=NOW)
    assert urls == {"https://x.com/recent"}


def test_load_sent_urls_normalizes(tmp_path: Path):
    path = tmp_path / "sent.jsonl"
    write_jsonl(
        path,
        [{"date": NOW.date().isoformat(), "url": "https://x.com/a?utm_source=rss"}],
    )
    urls = load_sent_urls(path, days=14, now=NOW)
    assert urls == {"https://x.com/a"}


def test_prune_history_drops_old(tmp_path: Path):
    path = tmp_path / "sent.jsonl"
    write_jsonl(
        path,
        [
            {"date": (NOW - timedelta(days=20)).date().isoformat(), "url": "old"},
            {"date": (NOW - timedelta(days=1)).date().isoformat(), "url": "new"},
        ],
    )
    kept = prune_history(path, days=14, now=NOW)
    assert kept == 1


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def test_dedupe_all_stage_order():
    items = [
        _item("Sent yesterday", "https://x.com/sent", published_at=NOW),
        _item("Dup title here", "https://x.com/dup1", weight=1.0, published_at=NOW),
        _item("Dup title here", "https://x.com/dup2", weight=2.0, published_at=NOW),
        _item("Old item", "https://x.com/old", published_at=NOW - timedelta(hours=48)),
        _item("Fresh unique", "https://x.com/fresh", published_at=NOW - timedelta(hours=1)),
    ]
    sent = {"https://x.com/sent"}
    out, counts = dedupe_all(items, sent, now=NOW)
    titles = {i["title"] for i in out}
    assert titles == {"Dup title here", "Fresh unique"}
    dup_kept = next(i for i in out if i["title"] == "Dup title here")
    assert dup_kept["url"] == "https://x.com/dup2"
    assert counts["dropped_sent_or_intra_batch"] == 1
    assert counts["dropped_similar"] == 1
    assert counts["dropped_stale"] == 1
