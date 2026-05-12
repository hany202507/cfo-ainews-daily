"""Three-stage dedupe pipeline (spec §5 [3]).

A) drop URLs already in sent_history (14-day rolling window)
B) drop near-duplicate titles via cosine similarity ≥ 0.85 (keep higher weight)
C) drop items older than 24h based on published_at

End-to-end entry point: ``dedupe_all(items, sent_urls, now=...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from scripts.fetch_rss import normalize_url
from scripts.state_store import (
    MAX_AGE_HOURS,
    SENT_HISTORY_DAYS,
    SIMILARITY_THRESHOLD,
    RawItem,
    SentHistoryRecord,
    append_jsonl,
    read_jsonl,
    write_jsonl,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sent history (Stage A)
# ---------------------------------------------------------------------------


def load_sent_urls(
    history_path: Path | str,
    days: int = SENT_HISTORY_DAYS,
    now: datetime | None = None,
) -> set[str]:
    """Return the set of URLs sent within the last ``days`` days (normalized)."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=days)).date()
    urls: set[str] = set()
    for record in read_jsonl(history_path):
        date_str = record.get("date")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str).date()
        except ValueError:
            continue
        if d >= cutoff:
            urls.add(normalize_url(record["url"]))
    return urls


def prune_history(
    history_path: Path | str,
    days: int = SENT_HISTORY_DAYS,
    now: datetime | None = None,
) -> int:
    """Rewrite history file with only entries within the window. Returns kept count."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=days)).date()
    kept: list[dict] = []
    for record in read_jsonl(history_path):
        try:
            d = datetime.fromisoformat(record["date"]).date()
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            kept.append(record)
    write_jsonl(history_path, kept)
    return len(kept)


def append_sent_records(
    history_path: Path | str, items: Iterable[RawItem | dict], date_str: str
) -> int:
    records: list[dict] = []
    for it in items:
        records.append(
            SentHistoryRecord(
                date=date_str,
                url=normalize_url(it["url"]),
                title_ko=it.get("title_ko") or it.get("title", ""),
            )
        )
    return append_jsonl(history_path, records)


# ---------------------------------------------------------------------------
# Stage A: URL match against sent history
# ---------------------------------------------------------------------------


def filter_by_sent_history(items: list[RawItem], sent_urls: set[str]) -> list[RawItem]:
    out: list[RawItem] = []
    seen_in_batch: set[str] = set()
    for item in items:
        url = normalize_url(item["url"])
        if url in sent_urls or url in seen_in_batch:
            continue
        seen_in_batch.add(url)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Stage B: title cosine similarity
# ---------------------------------------------------------------------------


def cosine_dedupe(items: list[RawItem], threshold: float = SIMILARITY_THRESHOLD) -> list[RawItem]:
    """For each pair with sim >= threshold, keep the higher-weight item.

    Uses char-ngrams (3–5) so it works for mixed KO/EN titles.
    """
    if len(items) < 2:
        return list(items)

    titles = [item["title"] for item in items]
    try:
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        matrix = vectorizer.fit_transform(titles)
    except ValueError:
        # all-empty vocabulary (e.g. very short titles) — nothing to compare
        return list(items)
    sim = cosine_similarity(matrix)

    n = len(items)
    drop: set[int] = set()
    for i in range(n):
        if i in drop:
            continue
        for j in range(i + 1, n):
            if j in drop:
                continue
            if sim[i, j] >= threshold:
                if items[i]["weight"] >= items[j]["weight"]:
                    drop.add(j)
                else:
                    drop.add(i)
                    break  # i is dropped; stop comparing it
    return [item for idx, item in enumerate(items) if idx not in drop]


# ---------------------------------------------------------------------------
# Stage C: age cutoff
# ---------------------------------------------------------------------------


def filter_by_age(
    items: list[RawItem], hours: int = MAX_AGE_HOURS, now: datetime | None = None
) -> list[RawItem]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    out: list[RawItem] = []
    for item in items:
        try:
            ts = datetime.fromisoformat(item["published_at"])
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts >= cutoff:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def dedupe_all(
    items: list[RawItem],
    sent_urls: set[str],
    now: datetime | None = None,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    max_age_hours: int = MAX_AGE_HOURS,
) -> tuple[list[RawItem], dict[str, int]]:
    """Run A → B → C in order. Returns (kept_items, stage_drop_counts)."""
    after_a = filter_by_sent_history(items, sent_urls)
    after_b = cosine_dedupe(after_a, threshold=similarity_threshold)
    after_c = filter_by_age(after_b, hours=max_age_hours, now=now)

    counts = {
        "input": len(items),
        "stage_a_kept": len(after_a),
        "stage_b_kept": len(after_b),
        "stage_c_kept": len(after_c),
        "dropped_sent_or_intra_batch": len(items) - len(after_a),
        "dropped_similar": len(after_a) - len(after_b),
        "dropped_stale": len(after_b) - len(after_c),
    }
    log.info("dedupe %s", counts)
    return after_c, counts
