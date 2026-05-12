"""Schema definitions, constants, and JSONL I/O for the daily pipeline.

Centralizes every threshold the spec calls out so they're not scattered as magic
numbers across the codebase.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

import jsonschema

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

Category = Literal[
    "ai_general",
    "ai_for_cfo",
    "ai_company_earnings",
    "ai_kr_companies",
    "ai_finance_jobs",
]

CATEGORIES: tuple[Category, ...] = (
    "ai_general",
    "ai_for_cfo",
    "ai_company_earnings",
    "ai_kr_companies",
    "ai_finance_jobs",
)

Lang = Literal["ko", "en"]


# ---------------------------------------------------------------------------
# TypedDicts (spec §4)
# ---------------------------------------------------------------------------


class RSSSource(TypedDict):
    name: str
    url: str
    category: Category
    language: Lang
    weight: float
    enabled: bool


class RawItem(TypedDict):
    source: str
    category: Category
    title: str
    url: str
    published_at: str  # ISO 8601
    summary: str | None
    language: Lang
    fetched_at: str  # ISO 8601
    weight: float


class FeedbackRecord(TypedDict):
    date: str  # YYYY-MM-DD
    item_url: str
    item_title_ko: str
    category: Category
    reaction: str  # e.g. "+1", "fire", "-1"
    count: int


class CuratedItem(TypedDict):
    rank: int
    group: str
    source_category: Category
    title_ko: str
    title_original: str | None
    one_liner_ko: str
    why_it_matters: str
    url: str
    source: str
    confidence: float


class SentHistoryRecord(TypedDict):
    date: str  # YYYY-MM-DD
    url: str
    title_ko: str


class LastMessage(TypedDict):
    date: str  # YYYY-MM-DD
    ts: str
    channel: str
    item_urls: list[str]


# ---------------------------------------------------------------------------
# Thresholds (spec §5, §7.2, §9)
# ---------------------------------------------------------------------------

# Curation / send gates (CFO digest defaults — see DigestConfig below for per-digest values)
TARGET_ITEMS: int = 10
MIN_ITEMS: int = 8  # below this, hold the send (§7.2)
GRIDIE_TARGET_ITEMS: int = 7
GRIDIE_MIN_ITEMS: int = 5

# Dedupe
SENT_HISTORY_DAYS: int = 14
SIMILARITY_THRESHOLD: float = 0.85
MAX_AGE_HOURS: int = 24  # §5 [3] step C

# Fact-check
FACT_CHECK_CONFIDENCE_THRESHOLD: float = 0.7
FACT_CHECK_REMOVE_RATIO_LIMIT: float = 0.5  # §7.2

# Cost guardrails
MAX_LLM_COST_USD: float = 2.0  # §7.2
MAX_LLM_CALLS: int = 15  # CLAUDE.md

# Slack
SLACK_BLOCKS_MAX: int = 50
SLACK_SECTION_TEXT_MAX: int = 3000

# RSS fetch
RSS_FETCH_PAR: int = 10
RSS_FETCH_TIMEOUT_SEC: int = 8
RSS_TOTAL_TIMEOUT_SEC: int = 90
RSS_DOMAIN_CONCURRENCY: int = 2
RSS_FAILURE_ABORT_RATIO: float = 0.5  # §5 [2]

# Fact-check concurrency
FACT_CHECK_PAR: int = 3

# Feedback look-back
FEEDBACK_RECENT_DAYS: int = 7

# Output retention
OUTPUTS_RETENTION_DAYS: int = 30

# Models
CURATE_MODEL: str = "claude-sonnet-4-6"
FACT_CHECK_MODEL: str = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# JSON Schema for curated payload (spec §4.2)
# ---------------------------------------------------------------------------

CURATED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": MIN_ITEMS,
            "maxItems": TARGET_ITEMS,
            "items": {
                "type": "object",
                "required": [
                    "rank",
                    "group",
                    "source_category",
                    "title_ko",
                    "one_liner_ko",
                    "why_it_matters",
                    "url",
                    "source",
                    "confidence",
                ],
                "properties": {
                    "rank": {"type": "integer", "minimum": 1, "maximum": TARGET_ITEMS},
                    "group": {"type": "string", "minLength": 1, "maxLength": 40},
                    "source_category": {"enum": list(CATEGORIES)},
                    "title_ko": {"type": "string", "minLength": 1, "maxLength": 60},
                    "title_original": {"type": ["string", "null"]},
                    "one_liner_ko": {"type": "string", "minLength": 1, "maxLength": 40},
                    "why_it_matters": {"type": "string", "minLength": 1, "maxLength": 80},
                    "url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https?://",
                    },
                    "source": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}


def validate_curated(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, error_message). format=uri is enforced via FormatChecker."""
    try:
        jsonschema.validate(
            payload,
            CURATED_SCHEMA,
            format_checker=jsonschema.FormatChecker(),
        )
        return True, ""
    except jsonschema.ValidationError as e:
        return False, e.message


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def read_jsonl(path: Path | str) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path | str, items: Iterable[dict[str, Any]]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count


def append_jsonl(path: Path | str, items: Iterable[dict[str, Any]]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_json(path: Path | str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path | str, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
OUTPUTS_DIR: Path = REPO_ROOT / "outputs" / "daily"
PAUSE_FILE: Path = REPO_ROOT / "PAUSE"

RSS_SOURCES_FILE: Path = DATA_DIR / "rss_sources.yaml"
FEEDBACK_FILE: Path = DATA_DIR / "feedback.jsonl"  # shared across digests

# Per-digest state — see DigestConfig
SENT_HISTORY_FILE_CFO: Path = DATA_DIR / "sent_history_cfo.jsonl"
SENT_HISTORY_FILE_GRIDIE: Path = DATA_DIR / "sent_history_gridie.jsonl"
LAST_MESSAGE_FILE_CFO: Path = DATA_DIR / "last_message_cfo.json"
LAST_MESSAGE_FILE_GRIDIE: Path = DATA_DIR / "last_message_gridie.json"

# Back-compat aliases (default to CFO so existing CFO callers/tests keep working)
SENT_HISTORY_FILE: Path = SENT_HISTORY_FILE_CFO
LAST_MESSAGE_FILE: Path = LAST_MESSAGE_FILE_CFO


def daily_dir(date_str: str) -> Path:
    """outputs/daily/YYYY-MM-DD/ — caller mkdirs."""
    return OUTPUTS_DIR / date_str


# ---------------------------------------------------------------------------
# Gridie digest schema
# ---------------------------------------------------------------------------

GRIDIE_CURATED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": GRIDIE_MIN_ITEMS,
            "maxItems": GRIDIE_TARGET_ITEMS,
            "items": {
                "type": "object",
                "required": [
                    "rank",
                    "source_category",
                    "title_ko",
                    "summary_ko",
                    "gridie_perspective",
                    "source_name",
                    "url",
                    "confidence",
                ],
                "properties": {
                    "rank": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": GRIDIE_TARGET_ITEMS,
                    },
                    "source_category": {"enum": list(CATEGORIES)},
                    "title_ko": {"type": "string", "minLength": 1, "maxLength": 80},
                    "summary_ko": {"type": "string", "minLength": 1, "maxLength": 150},
                    "gridie_perspective": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "source_name": {"type": "string", "minLength": 1, "maxLength": 40},
                    "url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https?://",
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        }
    },
}


def validate_curated_with_schema(
    payload: dict[str, Any], schema: dict[str, Any]
) -> tuple[bool, str]:
    """Generic validator. Use this for digest-specific schemas."""
    try:
        jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
        return True, ""
    except jsonschema.ValidationError as e:
        return False, e.message


# ---------------------------------------------------------------------------
# Digest configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestConfig:
    id: str  # "cfo" | "gridie"
    target_items: int
    min_items: int
    prompt_file: str  # filename inside prompts/
    schema: dict[str, Any]
    sent_history_file: Path
    last_message_file: Path
    header_emoji: str
    header_label: str

    @property
    def item_schema(self) -> dict[str, Any]:
        return self.schema["properties"]["items"]["items"]


_CFO_CONFIG = DigestConfig(
    id="cfo",
    target_items=TARGET_ITEMS,
    min_items=MIN_ITEMS,
    prompt_file="curate.md",
    schema=CURATED_SCHEMA,
    sent_history_file=SENT_HISTORY_FILE_CFO,
    last_message_file=LAST_MESSAGE_FILE_CFO,
    header_emoji="🌅",
    header_label="CFO AI Daily",
)

_GRIDIE_CONFIG = DigestConfig(
    id="gridie",
    target_items=GRIDIE_TARGET_ITEMS,
    min_items=GRIDIE_MIN_ITEMS,
    prompt_file="curate_gridie.md",
    schema=GRIDIE_CURATED_SCHEMA,
    sent_history_file=SENT_HISTORY_FILE_GRIDIE,
    last_message_file=LAST_MESSAGE_FILE_GRIDIE,
    header_emoji="🤖",
    header_label="Gridie AI Trend",
)

DIGESTS: dict[str, DigestConfig] = {
    "cfo": _CFO_CONFIG,
    "gridie": _GRIDIE_CONFIG,
}


def get_digest(digest_id: str) -> DigestConfig:
    if digest_id not in DIGESTS:
        raise ValueError(f"unknown digest: {digest_id!r}. valid: {list(DIGESTS)}")
    return DIGESTS[digest_id]
