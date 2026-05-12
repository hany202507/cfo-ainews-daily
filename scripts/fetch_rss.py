"""Async RSS/Atom fetch with PAR:10 + per-domain throttle.

Public entry: ``fetch_all(sources)`` → ``(raw_items, failed_source_names)``.

CLI:
    python -m scripts.fetch_rss --validate           # validate all enabled sources
    python -m scripts.fetch_rss --validate --source URL
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from time import mktime
from urllib.parse import parse_qsl, urldefrag, urlencode, urlparse, urlunparse

import feedparser
import httpx
import yaml
from bs4 import BeautifulSoup

from scripts.state_store import (
    CATEGORIES,
    RSS_DOMAIN_CONCURRENCY,
    RSS_FETCH_PAR,
    RSS_FETCH_TIMEOUT_SEC,
    RSS_SOURCES_FILE,
    RSS_TOTAL_TIMEOUT_SEC,
    RawItem,
    RSSSource,
)

log = logging.getLogger(__name__)

USER_AGENT = "cfo-ainews-daily/0.1 (+https://github.com/hany202507)"

# Query parameters stripped during URL normalization
_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_", "ref_")
_TRACKING_PARAM_NAMES: frozenset[str] = frozenset(
    {"fbclid", "gclid", "mc_cid", "mc_eid", "yclid", "msclkid"}
)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Drop tracking params and fragment. Lowercase scheme/host. Preserve path."""
    parsed = urlparse(url.strip())
    cleaned_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
        and k.lower() not in _TRACKING_PARAM_NAMES
    ]
    new = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(cleaned_query),
        fragment="",
    )
    return urldefrag(urlunparse(new)).url


def strip_html(text: str | None) -> str | None:
    if not text:
        return None
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True) or None


def _entry_published_at(entry, fallback: str) -> str:
    """Return ISO 8601 UTC. Falls back to ``fallback`` (already ISO)."""
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            dt = datetime.fromtimestamp(mktime(value), tz=UTC)
            return dt.isoformat()
    return fallback


def parse_feed_bytes(content: bytes, source: RSSSource, fetched_at: str) -> list[RawItem]:
    """Parse feed bytes into RawItems. Tolerates malformed feeds."""
    parsed = feedparser.parse(content)
    items: list[RawItem] = []
    for entry in parsed.entries or []:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        items.append(
            RawItem(
                source=source["name"],
                category=source["category"],
                title=title.strip(),
                url=normalize_url(url),
                published_at=_entry_published_at(entry, fetched_at),
                summary=strip_html(entry.get("summary") or entry.get("description")),
                language=source["language"],
                fetched_at=fetched_at,
                weight=float(source.get("weight", 1.0)),
            )
        )
    return items


def load_sources(path=None) -> list[RSSSource]:
    """Load enabled sources from data/rss_sources.yaml."""
    p = path or RSS_SOURCES_FILE
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    raw = doc.get("sources", []) or []
    sources: list[RSSSource] = []
    for entry in raw:
        if not entry.get("enabled", True):
            continue
        if entry.get("category") not in CATEGORIES:
            log.warning(
                "source %r has invalid category %r — skipping",
                entry.get("name"),
                entry.get("category"),
            )
            continue
        sources.append(
            RSSSource(
                name=entry["name"],
                url=entry["url"],
                category=entry["category"],
                language=entry.get("language", "en"),
                weight=float(entry.get("weight", 1.0)),
                enabled=True,
            )
        )
    return sources


# ---------------------------------------------------------------------------
# Async fetch
# ---------------------------------------------------------------------------


async def _fetch_one(
    client: httpx.AsyncClient,
    source: RSSSource,
    global_sem: asyncio.Semaphore,
    domain_sems: dict[str, asyncio.Semaphore],
    fetched_at: str,
) -> tuple[str, list[RawItem], Exception | None]:
    domain = urlparse(source["url"]).netloc.lower()
    domain_sem = domain_sems.setdefault(domain, asyncio.Semaphore(RSS_DOMAIN_CONCURRENCY))
    async with global_sem, domain_sem:
        try:
            resp = await client.get(source["url"], timeout=RSS_FETCH_TIMEOUT_SEC)
            resp.raise_for_status()
            items = parse_feed_bytes(resp.content, source, fetched_at)
            return source["name"], items, None
        except Exception as exc:  # noqa: BLE001 — broad on purpose: log + continue
            log.warning("fetch failed: %s — %r", source["name"], exc)
            return source["name"], [], exc


async def fetch_all(sources: Iterable[RSSSource]) -> tuple[list[RawItem], list[str]]:
    """Fetch every source in parallel; return (items, failed_source_names)."""
    src_list = list(sources)
    if not src_list:
        return [], []

    fetched_at = datetime.now(UTC).isoformat()
    global_sem = asyncio.Semaphore(RSS_FETCH_PAR)
    domain_sems: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(RSS_DOMAIN_CONCURRENCY)
    )

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        coros = [_fetch_one(client, src, global_sem, domain_sems, fetched_at) for src in src_list]
        try:
            results = await asyncio.wait_for(asyncio.gather(*coros), timeout=RSS_TOTAL_TIMEOUT_SEC)
        except TimeoutError:
            log.error("fetch_all exceeded total timeout %ds", RSS_TOTAL_TIMEOUT_SEC)
            return [], [s["name"] for s in src_list]

    all_items: list[RawItem] = []
    failed: list[str] = []
    seen_keys: set[tuple[str, str]] = set()  # (source, normalized url)
    for name, items, err in results:
        if err is not None:
            failed.append(name)
            continue
        for item in items:
            key = (item["source"], item["url"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_items.append(item)
    return all_items, failed


# ---------------------------------------------------------------------------
# CLI: --validate
# ---------------------------------------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate RSS sources.")
    parser.add_argument("--validate", action="store_true", required=True)
    parser.add_argument("--source", help="Validate a single URL instead of the YAML.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.source:
        sources = [
            RSSSource(
                name=args.source,
                url=args.source,
                category="ai_general",
                language="en",
                weight=1.0,
                enabled=True,
            )
        ]
    else:
        sources = load_sources()

    if not sources:
        print("no enabled sources to validate", file=sys.stderr)
        return 1

    items, failed = asyncio.run(fetch_all(sources))
    by_source: dict[str, int] = defaultdict(int)
    for it in items:
        by_source[it["source"]] += 1

    print(f"sources: {len(sources)}  ok: {len(sources) - len(failed)}  failed: {len(failed)}")
    for src in sources:
        n = by_source.get(src["name"], 0)
        status = "FAIL" if src["name"] in failed else f"{n:4d} entries"
        print(f"  [{status:>12}] {src['name']} — {src['url']}")

    if args.source and items:
        print("\nFirst 5 entries:")
        for it in items[:5]:
            print(f"  - {it['title']}\n    {it['url']}")

    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(_cli())
