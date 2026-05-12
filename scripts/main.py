"""Daily orchestration — implements the Run Order from spec §5.

Exit codes:
    0 — success (sent, dry-run completed, or PAUSED)
    1 — abort (missing secrets, RSS half failed, curation failed, send fail, etc.)
    2 — held (not enough items / fact-check majority remove) — same as failed for
        cron, but distinct in logs and last_message untouched
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from scripts.build_blocks import build_messages_for
from scripts.curate import curate
from scripts.dedupe import (
    append_sent_records,
    dedupe_all,
    load_sent_urls,
    prune_history,
)
from scripts.fact_check import fact_check_items
from scripts.fetch_rss import fetch_all, load_sources
from scripts.fetch_web_search import fetch_supplemental
from scripts.slack_feedback import collect_yesterday_feedback
from scripts.slack_send import SendOutcome, send_daily
from scripts.state_store import (
    DIGESTS,
    FEEDBACK_FILE,
    MAX_LLM_COST_USD,
    OUTPUTS_DIR,
    OUTPUTS_RETENTION_DAYS,
    PAUSE_FILE,
    RSS_FAILURE_ABORT_RATIO,
    daily_dir,
    get_digest,
    read_jsonl,
    write_json,
    write_jsonl,
)

log = logging.getLogger("cfo_ainews_daily.main")


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def _today_str(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).date().isoformat()


def _check_env(dry_run: bool) -> list[str]:
    """Return list of missing env vars (empty if OK). dry_run skips Slack vars."""
    required = ["ANTHROPIC_API_KEY"]
    if not dry_run:
        required += ["SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"]
    return [k for k in required if not os.environ.get(k)]


def _is_github_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _git_commit_and_push() -> None:
    """Commit data/ updates and push. Best-effort: warnings only on failure."""
    try:
        subprocess.run(["git", "add", "data/"], check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            log.info("git: nothing to commit")
            return
        today = _today_str()
        subprocess.run(
            ["git", "commit", "-m", f"daily: {today}"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        log.info("git: pushed state update")
    except subprocess.CalledProcessError as exc:
        log.warning("git push failed: %s", exc)
    except FileNotFoundError:
        log.warning("git not available — skipping state push")


def _cleanup_old_outputs(now: datetime | None = None) -> int:
    """Drop outputs/daily/<date>/ folders older than retention window."""
    if not OUTPUTS_DIR.exists():
        return 0
    cutoff = (now or datetime.now(UTC)).date() - timedelta(days=OUTPUTS_RETENTION_DAYS)
    removed = 0
    for child in OUTPUTS_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            d = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Pipeline (Run Order)
# ---------------------------------------------------------------------------


def run(
    *,
    digest: str = "cfo",
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> int:
    """Execute the Run Order for the given digest. Returns process exit code."""
    cfg = get_digest(digest)
    now = now or datetime.now(UTC)
    today = _today_str(now)
    out_dir = daily_dir(today) / cfg.id
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("starting digest=%s dry_run=%s force=%s today=%s", cfg.id, dry_run, force, today)

    # [0] PAUSE + env check ---------------------------------------------------
    if PAUSE_FILE.exists():
        log.info("PAUSED — skipping %s run for %s", cfg.id, today)
        return 0

    missing = _check_env(dry_run)
    if missing:
        log.error("missing required env vars: %s", missing)
        return 1

    # [1] Yesterday's feedback ------------------------------------------------
    if not dry_run:
        try:
            n_fb = collect_yesterday_feedback(last_message_path=cfg.last_message_file)
            log.info("collected %d feedback records", n_fb)
        except Exception as exc:  # noqa: BLE001
            log.warning("feedback collection failed: %r", exc)

    # [2] RSS fetch -----------------------------------------------------------
    sources = load_sources()
    if not sources:
        log.error("no enabled RSS sources in data/rss_sources.yaml")
        return 1
    raw_items, failed = asyncio.run(fetch_all(sources))
    log.info("RSS: %d sources, %d failed, %d items", len(sources), len(failed), len(raw_items))
    if not force and len(failed) / len(sources) >= RSS_FAILURE_ABORT_RATIO:
        log.error(
            "≥%.0f%% sources failed (%d/%d) — aborting",
            RSS_FAILURE_ABORT_RATIO * 100,
            len(failed),
            len(sources),
        )
        return 1
    write_jsonl(out_dir / "01_raw.jsonl", raw_items)

    # [3] Dedupe --------------------------------------------------------------
    sent_urls = load_sent_urls(cfg.sent_history_file, now=now)
    deduped, counts = dedupe_all(raw_items, sent_urls, now=now)
    log.info("dedupe: %s", counts)
    write_jsonl(out_dir / "02_deduped.jsonl", deduped)

    # Web-search supplement when candidate pool is short ---------------------
    if not dry_run and len(deduped) < cfg.target_items:
        shortfall = cfg.target_items - len(deduped)
        try:
            supp = fetch_supplemental(
                target_n=shortfall,
                existing_urls=[i["url"] for i in deduped],
                now=now,
            )
            if supp["items"]:
                deduped.extend(supp["items"])
                write_jsonl(out_dir / "02_deduped.jsonl", deduped)
                log.info(
                    "web_search supplement: +%d items (cost $%.4f)",
                    len(supp["items"]),
                    supp["cost_usd"],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("web_search supplement failed: %r", exc)

    if len(deduped) < cfg.min_items:
        log.error(
            "only %d items after dedupe (< MIN_ITEMS=%d) — holding send",
            len(deduped),
            cfg.min_items,
        )
        return 2

    # [4] Curate --------------------------------------------------------------
    feedback = list(read_jsonl(FEEDBACK_FILE))
    curation = curate(deduped, feedback, today, config=cfg)
    log.info(
        "curate: status=%s cost=$%.4f errors=%s",
        curation["status"],
        curation["cost_usd"],
        curation["errors"],
    )
    if curation["status"] == "failed" or curation["payload"] is None:
        log.error("curation failed — holding send")
        return 2

    payload = curation["payload"]
    write_json(out_dir / "03_curated.json", payload)
    total_cost = curation["cost_usd"]

    # [5] Fact-check (conditional) -------------------------------------------
    items = payload["items"]
    fc = fact_check_items(items)
    total_cost += fc["cost_usd"]
    log.info(
        "fact_check: removed=%d cost=$%.4f abort=%s",
        len(fc["removed"]),
        fc["cost_usd"],
        fc["abort_send"],
    )
    if fc["abort_send"]:
        log.error("fact-check majority-remove triggered — holding send")
        return 2
    items = fc["items"]
    if len(items) < cfg.min_items:
        log.error(
            "only %d items survived fact-check (< MIN_ITEMS=%d) — holding send",
            len(items),
            cfg.min_items,
        )
        return 2
    payload = {"items": items}
    write_json(out_dir / "03_curated.json", payload)

    # Cost guardrail ---------------------------------------------------------
    if total_cost > MAX_LLM_COST_USD:
        log.error("LLM cost $%.4f exceeded cap $%.2f — holding send", total_cost, MAX_LLM_COST_USD)
        return 2

    # [6] Build Slack blocks --------------------------------------------------
    messages = build_messages_for(cfg, items, today)
    write_json(out_dir / "04_slack_blocks.json", {"messages": messages})

    # [7] Human gate — covered by PAUSE in [0] -------------------------------
    # (no additional human approval in automated mode)

    # [8] Send ---------------------------------------------------------------
    item_urls = [i["url"] for i in items]
    outcome, _ = send_daily(
        messages,
        item_urls,
        dry_run=dry_run,
        now=now,
        last_message_path=cfg.last_message_file,
    )
    log.info("send outcome: %s", outcome)

    # [9] Persist + cleanup --------------------------------------------------
    if outcome == SendOutcome.SENT:
        append_sent_records(cfg.sent_history_file, items, today)
        prune_history(cfg.sent_history_file, now=now)
        removed_dirs = _cleanup_old_outputs(now=now)
        if removed_dirs:
            log.info("cleanup: removed %d old daily output dirs", removed_dirs)
        if _is_github_actions():
            _git_commit_and_push()
        else:
            log.info("not in GitHub Actions — skipping git commit/push")

    log.info(
        "done: digest=%s outcome=%s items=%d total_cost=$%.4f",
        cfg.id,
        outcome,
        len(items),
        total_cost,
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scripts.main", description="CFO AI Daily orchestrator")
    p.add_argument(
        "--digest",
        choices=list(DIGESTS),
        default="cfo",
        help="Which digest to run (default: cfo)",
    )
    p.add_argument("--dry-run", action="store_true", help="Skip Slack send")
    p.add_argument(
        "--force",
        action="store_true",
        help="Continue even when RSS failure ratio crosses the abort threshold",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)
    return run(digest=args.digest, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
