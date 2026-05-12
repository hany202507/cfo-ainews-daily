"""End-to-end integration tests for the orchestrator and parallel-consistency
of the RSS fetch path (spec §15 Test 4 + Test 5)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import httpx

import scripts.main as main_mod
from scripts.fetch_rss import _fetch_one
from scripts.state_store import RSSSource

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_FEED = (FIXTURES / "rss_sample.xml").read_bytes()


# ---------------------------------------------------------------------------
# Test 4: PAUSE gate (§15)
# ---------------------------------------------------------------------------


def _stub_all_pipeline_modules(monkeypatch):
    """Replace every pipeline call with a MagicMock so we can prove they aren't
    invoked when PAUSE short-circuits."""
    mocks = {
        "fetch_all": MagicMock(),
        "load_sources": MagicMock(return_value=[]),
        "dedupe_all": MagicMock(),
        "load_sent_urls": MagicMock(return_value=set()),
        "curate": MagicMock(),
        "fact_check_items": MagicMock(),
        "fetch_supplemental": MagicMock(),
        "build_messages_for": MagicMock(),
        "send_daily": MagicMock(),
        "collect_yesterday_feedback": MagicMock(),
        "append_sent_records": MagicMock(),
        "prune_history": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(main_mod, name, m)
    return mocks


def test_paused_returns_zero_and_calls_nothing(tmp_path, monkeypatch):
    pause = tmp_path / "PAUSE"
    pause.write_text("", encoding="utf-8")
    monkeypatch.setattr(main_mod, "PAUSE_FILE", pause)

    mocks = _stub_all_pipeline_modules(monkeypatch)

    exit_code = main_mod.run(dry_run=False)
    assert exit_code == 0
    for name, m in mocks.items():
        m.assert_not_called(), f"{name} should not be called when PAUSED"


def test_unpaused_does_call_pipeline_when_no_pause_file(tmp_path, monkeypatch):
    """Sanity check: if PAUSE doesn't exist, the pipeline starts (env-check stops
    it before any real call, so we only assert exit code 1 from missing secrets)."""
    pause = tmp_path / "PAUSE"  # NOT created
    monkeypatch.setattr(main_mod, "PAUSE_FILE", pause)
    monkeypatch.setattr(main_mod, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)

    exit_code = main_mod.run(dry_run=False)
    # PAUSE not present → proceeds past [0], hits env check → exit 1
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Test 5: PAR vs SEQ consistency
# ---------------------------------------------------------------------------


def _source(name: str, url: str) -> RSSSource:
    return RSSSource(
        name=name,
        url=url,
        category="ai_general",
        language="en",
        weight=1.0,
        enabled=True,
    )


def _mock_transport_for(responses: dict[str, bytes]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = responses.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


async def _fetch_with_concurrency(sources, responses, par: int):
    """Run the production _fetch_one path with a chosen global_sem cap."""
    global_sem = asyncio.Semaphore(par)
    domain_sems: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(par))
    fetched_at = "2026-05-12T00:00:00+00:00"

    async with httpx.AsyncClient(transport=_mock_transport_for(responses)) as client:
        results = await asyncio.gather(
            *[_fetch_one(client, s, global_sem, domain_sems, fetched_at) for s in sources]
        )

    items = []
    failed = []
    for name, fetched, err in results:
        if err is not None:
            failed.append(name)
        else:
            items.extend(fetched)
    return items, failed


def test_par_10_and_seq_1_produce_same_item_set():
    """For identical input + responses, PAR:10 and serial PAR:1 must produce
    the same (source, url) set."""
    sources = [_source(f"src-{i}", f"https://feed-{i}.example.com/rss") for i in range(30)]
    responses = {s["url"]: SAMPLE_FEED for s in sources}

    par_items, par_failed = asyncio.run(_fetch_with_concurrency(sources, responses, par=10))
    seq_items, seq_failed = asyncio.run(_fetch_with_concurrency(sources, responses, par=1))

    par_keys = {(i["source"], i["url"]) for i in par_items}
    seq_keys = {(i["source"], i["url"]) for i in seq_items}
    assert par_keys == seq_keys
    assert par_failed == seq_failed == []
    # Every source produced 3 entries from the fixture
    assert len(par_keys) == len(sources) * 3


def test_par_consistency_under_partial_failures():
    """A consistent subset of sources fails the same way under both PAR settings."""
    sources = [_source(f"src-{i}", f"https://feed-{i}.example.com/rss") for i in range(10)]
    # only half the URLs are mapped → the other half 404
    responses = {s["url"]: SAMPLE_FEED for s in sources[:5]}

    par_items, par_failed = asyncio.run(_fetch_with_concurrency(sources, responses, par=10))
    seq_items, seq_failed = asyncio.run(_fetch_with_concurrency(sources, responses, par=1))
    assert {(i["source"], i["url"]) for i in par_items} == {
        (i["source"], i["url"]) for i in seq_items
    }
    assert sorted(par_failed) == sorted(seq_failed)
    assert len(par_failed) == 5
