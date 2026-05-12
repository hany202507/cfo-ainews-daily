"""Unit tests for fetch_rss — URL normalization, HTML stripping, feed parsing,
async fetch with mocked HTTP, and YAML source loading."""

from __future__ import annotations

import asyncio
from datetime import UTC
from pathlib import Path

import httpx

from scripts.fetch_rss import (
    _fetch_one,
    load_sources,
    normalize_url,
    parse_feed_bytes,
    strip_html,
)
from scripts.state_store import RSSSource

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_FEED_BYTES = (FIXTURES / "rss_sample.xml").read_bytes()


def _source(**overrides) -> RSSSource:
    base = RSSSource(
        name="Test Source",
        url="https://example.com/feed.xml",
        category="ai_general",
        language="en",
        weight=1.0,
        enabled=True,
    )
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------


def test_normalize_url_strips_utm_params():
    out = normalize_url("https://x.com/a?utm_source=rss&utm_medium=feed&id=1")
    assert out == "https://x.com/a?id=1"


def test_normalize_url_strips_ref_and_fbclid():
    out = normalize_url("https://x.com/a?ref_=home&fbclid=abc&q=keep")
    assert out == "https://x.com/a?q=keep"


def test_normalize_url_strips_fragment():
    assert normalize_url("https://x.com/a#section") == "https://x.com/a"


def test_normalize_url_lowercases_host():
    assert normalize_url("https://X.COM/Path") == "https://x.com/Path"


def test_normalize_url_no_query_after_strip():
    assert normalize_url("https://x.com/a?utm_source=rss") == "https://x.com/a"


def test_normalize_url_preserves_other_query():
    assert normalize_url("https://x.com/a?page=2&utm_x=1") == "https://x.com/a?page=2"


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_none_passthrough():
    assert strip_html(None) is None
    assert strip_html("") is None


def test_strip_html_collapses_whitespace():
    assert strip_html("<p>A</p><p>B</p>") == "A B"


# ---------------------------------------------------------------------------
# parse_feed_bytes
# ---------------------------------------------------------------------------


def test_parse_feed_bytes_basic():
    src = _source()
    items = parse_feed_bytes(SAMPLE_FEED_BYTES, src, fetched_at="2026-05-12T00:00:00+00:00")
    assert len(items) == 3
    titles = [i["title"] for i in items]
    assert "NVIDIA Q4 Data Center Revenue Up 47% YoY" in titles


def test_parse_feed_bytes_url_normalization_applied():
    src = _source()
    items = parse_feed_bytes(SAMPLE_FEED_BYTES, src, fetched_at="2026-05-12T00:00:00+00:00")
    nvda = next(i for i in items if i["title"].startswith("NVIDIA"))
    assert "utm_" not in nvda["url"]
    assert "ref_" not in nvda["url"]
    assert nvda["url"] == "https://example.com/articles/nvda-q4"


def test_parse_feed_bytes_strips_summary_html():
    src = _source()
    items = parse_feed_bytes(SAMPLE_FEED_BYTES, src, fetched_at="2026-05-12T00:00:00+00:00")
    nvda = next(i for i in items if i["title"].startswith("NVIDIA"))
    assert nvda["summary"] is not None
    assert "<" not in nvda["summary"] and ">" not in nvda["summary"]
    assert "47%" in nvda["summary"]


def test_parse_feed_bytes_no_summary_field():
    src = _source()
    items = parse_feed_bytes(SAMPLE_FEED_BYTES, src, fetched_at="2026-05-12T00:00:00+00:00")
    samsung = next(i for i in items if i["title"].startswith("Samsung"))
    assert samsung["summary"] is None


def test_parse_feed_bytes_inherits_source_metadata():
    src = _source(category="ai_kr_companies", language="ko", weight=1.7)
    items = parse_feed_bytes(SAMPLE_FEED_BYTES, src, fetched_at="2026-05-12T00:00:00+00:00")
    for item in items:
        assert item["category"] == "ai_kr_companies"
        assert item["language"] == "ko"
        assert item["weight"] == 1.7
        assert item["source"] == "Test Source"


def test_parse_feed_bytes_uses_fetched_at_when_no_pubdate():
    src = _source()
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>x</title><link>https://x.com</link><description>x</description>
      <item><title>No date</title><link>https://x.com/a</link></item>
    </channel></rss>"""
    fetched_at = "2026-05-12T00:00:00+00:00"
    items = parse_feed_bytes(feed, src, fetched_at=fetched_at)
    assert items[0]["published_at"] == fetched_at


def test_parse_feed_bytes_skips_entries_without_link_or_title():
    src = _source()
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>x</title><link>https://x.com</link><description>x</description>
      <item><title>No link</title></item>
      <item><link>https://x.com/a</link></item>
      <item><title>Good</title><link>https://x.com/b</link></item>
    </channel></rss>"""
    items = parse_feed_bytes(feed, src, fetched_at="2026-05-12T00:00:00+00:00")
    assert len(items) == 1
    assert items[0]["title"] == "Good"


# ---------------------------------------------------------------------------
# fetch_all with mock transport
# ---------------------------------------------------------------------------


def _mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        if key in responses:
            return responses[key]
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _run_fetch_with_mock(sources, responses):
    """Mirror fetch_all but inject a mock httpx transport."""
    from collections import defaultdict
    from datetime import datetime

    fetched_at = datetime.now(UTC).isoformat()
    global_sem = asyncio.Semaphore(10)
    domain_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(2))

    async with httpx.AsyncClient(transport=_mock_transport(responses)) as client:
        results = await asyncio.gather(
            *[_fetch_one(client, s, global_sem, domain_sems, fetched_at) for s in sources]
        )

    items: list = []
    failed: list[str] = []
    for name, fetched, err in results:
        if err is not None:
            failed.append(name)
        else:
            items.extend(fetched)
    return items, failed


def test_fetch_all_aggregates_two_sources():
    s1 = _source(name="A", url="https://a.com/feed")
    s2 = _source(name="B", url="https://b.com/feed", category="ai_for_cfo")
    responses = {
        "https://a.com/feed": httpx.Response(200, content=SAMPLE_FEED_BYTES),
        "https://b.com/feed": httpx.Response(200, content=SAMPLE_FEED_BYTES),
    }
    items, failed = asyncio.run(_run_fetch_with_mock([s1, s2], responses))
    assert failed == []
    assert len(items) == 6
    assert {i["source"] for i in items} == {"A", "B"}


def test_fetch_all_partial_failure_does_not_abort():
    s_ok = _source(name="OK", url="https://ok.com/feed")
    s_bad = _source(name="BAD", url="https://bad.com/feed")
    responses = {
        "https://ok.com/feed": httpx.Response(200, content=SAMPLE_FEED_BYTES),
        "https://bad.com/feed": httpx.Response(500),
    }
    items, failed = asyncio.run(_run_fetch_with_mock([s_ok, s_bad], responses))
    assert failed == ["BAD"]
    assert len(items) == 3
    assert all(i["source"] == "OK" for i in items)


# ---------------------------------------------------------------------------
# load_sources
# ---------------------------------------------------------------------------


def test_load_sources_filters_disabled(tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        """sources:
  - name: "Keep Me"
    url: "https://keep.com/feed"
    category: ai_general
    language: en
    weight: 1.0
    enabled: true
  - name: "Skip Me"
    url: "https://skip.com/feed"
    category: ai_general
    language: en
    weight: 1.0
    enabled: false
""",
        encoding="utf-8",
    )
    sources = load_sources(yaml_path)
    assert [s["name"] for s in sources] == ["Keep Me"]


def test_load_sources_drops_invalid_category(tmp_path, caplog):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        """sources:
  - name: "Bad"
    url: "https://bad.com/feed"
    category: nope_not_real
    language: en
    weight: 1.0
    enabled: true
""",
        encoding="utf-8",
    )
    sources = load_sources(yaml_path)
    assert sources == []


def test_load_sources_defaults_enabled_true(tmp_path):
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        """sources:
  - name: "Default"
    url: "https://x.com/feed"
    category: ai_general
    language: en
    weight: 1.0
""",
        encoding="utf-8",
    )
    sources = load_sources(yaml_path)
    assert len(sources) == 1
