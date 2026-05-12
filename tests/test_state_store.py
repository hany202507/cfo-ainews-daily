"""Schema validation + JSONL round-trip tests for state_store."""

from __future__ import annotations

from pathlib import Path

from scripts.state_store import (
    CATEGORIES,
    CURATED_SCHEMA,
    MIN_ITEMS,
    TARGET_ITEMS,
    append_jsonl,
    read_json,
    read_jsonl,
    validate_curated,
    write_json,
    write_jsonl,
)


def _make_item(rank: int, **overrides) -> dict:
    item = {
        "rank": rank,
        "group": "어닝 시즌 핵심",
        "source_category": "ai_company_earnings",
        "title_ko": f"테스트 항목 {rank}",
        "title_original": "Test Item",
        "one_liner_ko": "한 줄 요약입니다",
        "why_it_matters": "CFO 관점에서 중요한 이유",
        "url": f"https://example.com/article-{rank}",
        "source": "Bloomberg",
        "confidence": 0.85,
    }
    item.update(overrides)
    return item


def _make_payload(n: int = TARGET_ITEMS) -> dict:
    return {"items": [_make_item(i) for i in range(1, n + 1)]}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_accepts_full_valid_payload():
    ok, err = validate_curated(_make_payload(TARGET_ITEMS))
    assert ok, err


def test_schema_accepts_min_items():
    ok, err = validate_curated(_make_payload(MIN_ITEMS))
    assert ok, err


def test_schema_rejects_too_few_items():
    ok, err = validate_curated(_make_payload(MIN_ITEMS - 1))
    assert not ok
    assert "short" in err.lower() or "min" in err.lower()


def test_schema_rejects_too_many_items():
    ok, _ = validate_curated(_make_payload(TARGET_ITEMS + 1))
    assert not ok


def test_schema_rejects_bad_category():
    payload = _make_payload()
    payload["items"][0]["source_category"] = "not_a_real_category"
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_rejects_confidence_out_of_range():
    payload = _make_payload()
    payload["items"][0]["confidence"] = 1.5
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_rejects_missing_required_field():
    payload = _make_payload()
    del payload["items"][0]["why_it_matters"]
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_rejects_oversized_title_ko():
    payload = _make_payload()
    payload["items"][0]["title_ko"] = "가" * 61  # max 60
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_rejects_oversized_one_liner():
    payload = _make_payload()
    payload["items"][0]["one_liner_ko"] = "가" * 41  # max 40
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_rejects_bad_url_format():
    payload = _make_payload()
    payload["items"][0]["url"] = "not-a-url"
    ok, _ = validate_curated(payload)
    assert not ok


def test_schema_allows_null_title_original():
    payload = _make_payload()
    payload["items"][0]["title_original"] = None
    ok, err = validate_curated(payload)
    assert ok, err


def test_categories_match_schema():
    enum_values = CURATED_SCHEMA["properties"]["items"]["items"]["properties"]["source_category"][
        "enum"
    ]
    assert set(enum_values) == set(CATEGORIES)


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------


def test_write_then_read_jsonl_roundtrip(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    items = [{"id": 1, "name": "한글-이름"}, {"id": 2, "name": "Bloomberg"}]
    n = write_jsonl(path, items)
    assert n == 2
    assert list(read_jsonl(path)) == items


def test_append_jsonl_adds_lines(tmp_path: Path):
    path = tmp_path / "append.jsonl"
    write_jsonl(path, [{"a": 1}])
    append_jsonl(path, [{"a": 2}, {"a": 3}])
    assert [d["a"] for d in read_jsonl(path)] == [1, 2, 3]


def test_read_jsonl_missing_file_returns_empty(tmp_path: Path):
    assert list(read_jsonl(tmp_path / "nope.jsonl")) == []


def test_read_jsonl_skips_blank_lines(tmp_path: Path):
    path = tmp_path / "blanks.jsonl"
    path.write_text('{"a":1}\n\n{"a":2}\n   \n', encoding="utf-8")
    assert [d["a"] for d in read_jsonl(path)] == [1, 2]


def test_jsonl_non_ascii_preserved(tmp_path: Path):
    path = tmp_path / "ko.jsonl"
    write_jsonl(path, [{"title": "🌅 CFO AI Daily — 안녕"}])
    raw = path.read_text(encoding="utf-8")
    assert "🌅" in raw and "안녕" in raw  # ensure_ascii=False


def test_read_json_missing_returns_none(tmp_path: Path):
    assert read_json(tmp_path / "missing.json") is None


def test_write_then_read_json_roundtrip(tmp_path: Path):
    path = tmp_path / "msg.json"
    payload = {"date": "2026-05-12", "ts": "1234.5678", "channel": "C123", "item_urls": []}
    write_json(path, payload)
    assert read_json(path) == payload
