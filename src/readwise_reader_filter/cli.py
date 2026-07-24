"""CLI for filtering and managing Readwise Reader feed entries."""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from readwise_reader_api import ReadwiseReaderAPIClient, ReadwiseReaderError

from .matching import entry_matches_rule, entry_matches_source
from .models import ReaderFeedEntry
from .parsing import load_config, validate_config
from .paths import action_log_path, api_cache_dir, config_path
from .types import FilterConfig, FilterConfigError, FilterRule, MatchResult

logger = logging.getLogger(__name__)

MAX_ACTION_LOG_BYTES = 5 * 1024 * 1024  # 5 MB

# Maps action name to a function that builds the update dict for that action
ACTION_TO_UPDATE = {
    "seen": lambda entry: {"id": entry.id, "seen": True},
    "unseen": lambda entry: {"id": entry.id, "seen": False},
    "archive": lambda entry: {"id": entry.id, "location": "archive"},
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="readwise-reader-filter",
        description="Filter and manage Readwise Reader feed entries",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to filter config YAML (env: READWISE_FILTER_CONFIG, "
            "default: ~/.config/readwise-reader-filter/config.yaml)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show actions without executing",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass cache and re-download all documents",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show detailed reasoning for each matched entry",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate config file and exit (no API calls made)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Execute actions without interactive confirmation prompt",
    )
    return parser


def _resolve_config_path(cli_path: str | None) -> str:
    if cli_path:
        return cli_path
    env_path = os.getenv("READWISE_FILTER_CONFIG")
    if env_path:
        logger.info("Using config from READWISE_FILTER_CONFIG env var: %s", env_path)
        return env_path
    return str(config_path())


def configure_logging(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def _config_needs_content_filter(config: FilterConfig) -> bool:
    """Check if any rule uses the content filter (requires html_content)."""
    all_rules = config.global_rules + [r for f in config.feeds for r in f.rules]
    return any(rule.content is not None for rule in all_rules)


def _fetch_entries(
    api: ReadwiseReaderAPIClient, refresh: bool, with_html_content: bool = False
) -> list[ReaderFeedEntry]:
    logger.info("Fetching documents…")
    docs = api.list_documents(
        location="feed", force_refresh=refresh, with_html_content=with_html_content
    )
    return [ReaderFeedEntry.from_api(d) for d in docs]


def _collect_actions(
    entries: list[ReaderFeedEntry],
    config: FilterConfig,
) -> tuple[list[tuple[ReaderFeedEntry, list[MatchResult]]], list[tuple[ReaderFeedEntry, FilterRule]]]:
    """Collect all actions for all entries.

    Returns:
        display_results: For display/debug (entry + all matching MatchResults)
        actions: Flat list of (entry, rule) for execution
    """
    display_results: list[tuple[ReaderFeedEntry, list[MatchResult]]] = []
    actions: list[tuple[ReaderFeedEntry, FilterRule]] = []

    for entry in entries:
        entry_matches: list[MatchResult] = []

        # mark_all_as_unseen: add unseen for all seen entries
        if config.mark_all_as_unseen and entry.first_opened_at is not None:
            unseen_rule = FilterRule(name="mark_all_as_unseen", action="unseen")
            entry_matches.append(
                MatchResult(scope="global", rule=unseen_rule, reasons=["seen entry"])
            )
            actions.append((entry, unseen_rule))

        # Global rules
        for rule in config.global_rules:
            ok, reasons = entry_matches_rule(entry, rule)
            if ok:
                entry_matches.append(
                    MatchResult(scope="global", rule=rule, reasons=reasons)
                )
                actions.append((entry, rule))

        # Feed rules
        for feed_def in config.feeds:
            ok, src_reasons = entry_matches_source(entry, feed_def.match)
            if not ok:
                continue
            for rule in feed_def.rules:
                ok, reasons = entry_matches_rule(entry, rule)
                if ok:
                    entry_matches.append(
                        MatchResult(
                            scope=feed_def.name,
                            rule=rule,
                            reasons=src_reasons + reasons,
                        )
                    )
                    actions.append((entry, rule))

        if entry_matches:
            display_results.append((entry, entry_matches))

    return display_results, actions


def _display_results(
    results: list[tuple[ReaderFeedEntry, list[MatchResult]]], debug: bool
):
    for entry, match_results in results:
        logger.info("  %s", entry.title)
        logger.info("    URL:  %s", entry.source_url)
        logger.info("    Feed: %s  |  Author: %s", entry.site_name, entry.author)
        for result in match_results:
            logger.info(
                "    -> [%s] %s (via %s)",
                result.rule.action,
                result.rule.name or "unnamed",
                result.scope,
            )
            if debug:
                for r in result.reasons:
                    logger.info("      %s", r)
        logger.info("")

    logger.info("Matched: %d entries", len(results))


def _log_actions(entry: ReaderFeedEntry, rules: list[FilterRule]) -> None:
    path = action_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "actions": [rule.action for rule in rules],
        "rules": [rule.name for rule in rules],
        "performed_at": datetime.now(timezone.utc).isoformat(),
        "entry": entry.raw,
    }
    try:
        if path.exists() and path.stat().st_size > MAX_ACTION_LOG_BYTES:
            rotated = path.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)
        with open(path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError):
        logger.warning("Failed to log action")


def _build_mark_for_delete_update(entry: ReaderFeedEntry, rule: FilterRule) -> dict:
    """Build update dict for mark_for_delete action."""
    current_tags = [t for t in (entry.tags or []) if t is not None]
    if not any(t.lower() == "delete" for t in current_tags):
        current_tags.append("delete")
    update: dict = {
        "id": entry.id,
        "tags": current_tags,
        "location": "later",
    }
    if rule.name:
        update["notes"] = f"DELETE: {rule.name}"
    return update


def _build_bulk_updates(
    api: ReadwiseReaderAPIClient,
    actions: list[tuple[ReaderFeedEntry, FilterRule]],
) -> list[tuple[ReaderFeedEntry, list[FilterRule], dict]]:
    """Group actions by entry, merging compatible updates into one dict per entry."""
    merged: dict[str, tuple[ReaderFeedEntry, list[FilterRule], dict]] = {}

    for entry, rule in actions:
        # mark_for_delete: skip if already marked
        if rule.action == "mark_for_delete":
            if api.is_marked_for_delete(entry.id):
                logger.info(
                    "  [mark_for_delete] %s — (already marked, skipping)",
                    entry.title,
                )
                continue
            update = _build_mark_for_delete_update(entry, rule)
        elif rule.action in ACTION_TO_UPDATE:
            update = ACTION_TO_UPDATE[rule.action](entry)
        else:
            continue

        if entry.id in merged:
            merged[entry.id][1].append(rule)
            merged[entry.id][2].update(update)
        else:
            merged[entry.id] = (entry, [rule], dict(update))

    return list(merged.values())


def _execute_deletes(
    api: ReadwiseReaderAPIClient,
    deletes: list[tuple[ReaderFeedEntry, FilterRule]],
) -> tuple[int, int]:
    """Execute individual deletes. Returns (executed, errors)."""
    executed = 0
    errors = 0
    for entry, rule in deletes:
        logger.info("  [delete] %s — %s", entry.title, entry.source_url)
        try:
            if not api.delete_document(entry.id):
                raise RuntimeError("delete_document returned False")
            _log_actions(entry, [rule])
            executed += 1
        except ReadwiseReaderError as e:
            logger.error("    ERROR: %s", e)
            errors += 1
        except Exception as e:
            logger.exception("    UNEXPECTED ERROR: %s", e)
            errors += 1
    return executed, errors


def _execute_bulk(
    api: ReadwiseReaderAPIClient,
    bulk_updates: list[tuple[ReaderFeedEntry, list[FilterRule], dict]],
) -> tuple[int, int]:
    """Execute bulk updates. Returns (executed, errors)."""
    if not bulk_updates:
        return 0, 0
    logger.info("  Bulk updating %d entries…", len(bulk_updates))
    try:
        updated = api.bulk_update([u for _, _, u in bulk_updates])
        for entry, rules, _ in bulk_updates:
            _log_actions(entry, rules)
        return updated, 0
    except ReadwiseReaderError as e:
        logger.error("    ERROR during bulk update: %s", e)
        return 0, 1
    except Exception as e:
        logger.exception("    UNEXPECTED ERROR during bulk update: %s", e)
        return 0, 1


def _execute_actions(
    api: ReadwiseReaderAPIClient,
    actions: list[tuple[ReaderFeedEntry, FilterRule]],
) -> int:
    """Execute all actions: bulk updates + individual deletes."""
    logger.info("\n=== Executing actions ===\n")

    deletes = [(e, r) for e, r in actions if r.action == "delete"]
    bulk = [(e, r) for e, r in actions if r.action != "delete"]

    bulk_updates = _build_bulk_updates(api, bulk)
    bulk_executed, bulk_errors = _execute_bulk(api, bulk_updates)
    delete_executed, delete_errors = _execute_deletes(api, deletes)

    executed = bulk_executed + delete_executed
    errors = bulk_errors + delete_errors

    logger.info("\nDone. %d actions executed, %d errors.", executed, errors)
    return 0 if errors == 0 else 1


def _confirm_execution() -> bool:
    try:
        answer = input("\nProceed with actions? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        logger.info("\nAborted.")
        return False
    if answer not in ("y", "yes"):
        logger.info("Aborted.")
        return False
    return True


def _load_config_or_exit(path: str) -> FilterConfig:
    try:
        return load_config(path)
    except FilterConfigError as e:
        logger.error("Config error: %s", e)
        sys.exit(1)


def _build_api_client_or_exit() -> ReadwiseReaderAPIClient:
    try:
        return ReadwiseReaderAPIClient(
            use_cache=True,
            cache_dir=str(api_cache_dir()),
        )
    except ReadwiseReaderError as e:
        logger.error("Error: %s", e)
        sys.exit(1)


def _fetch_entries_or_exit(
    api: ReadwiseReaderAPIClient, refresh: bool, with_html_content: bool = False
) -> list[ReaderFeedEntry]:
    try:
        return _fetch_entries(api, refresh, with_html_content)
    except Exception as e:
        logger.error("Failed to fetch documents: %s", e)
        sys.exit(1)


def _execute_actions_or_exit(
    api: ReadwiseReaderAPIClient,
    actions: list[tuple[ReaderFeedEntry, FilterRule]],
) -> int:
    try:
        return _execute_actions(api, actions)
    except Exception as e:
        logger.error("Failed to execute actions: %s", e)
        return 1


def _validate(config_path: str) -> int:
    try:
        errors = validate_config(config_path)
    except FilterConfigError as e:
        logger.error("Config error: %s", e)
        return 1
    if errors:
        logger.error(
            "Config file has %d error(s). Fix the issues above.", len(errors)
        )
        return 1
    return 0


def _run(args: argparse.Namespace) -> int:
    config_path_resolved = _resolve_config_path(args.config)
    logger.info("Using config: %s", config_path_resolved)

    if args.validate:
        return _validate(config_path_resolved)

    config = _load_config_or_exit(config_path_resolved)
    if not config.mark_all_as_unseen and not config.global_rules and not config.feeds:
        logger.info("No filter rules defined in config.")
        return 0

    api = _build_api_client_or_exit()

    needs_content = _config_needs_content_filter(config)
    if needs_content:
        logger.info("Content filter detected — fetching full article content")
    entries = _fetch_entries_or_exit(api, args.refresh, needs_content)

    if not entries:
        logger.info("No documents found.")
        return 0

    logger.info("\nDocuments: %d total\n", len(entries))

    display_results, actions = _collect_actions(entries, config)

    if display_results:
        _display_results(display_results, args.debug)

    if args.dry_run or not actions:
        if not actions:
            logger.info("No entries matched any filter rule.")
        else:
            logger.info("\nDry-run mode. No actions executed.")
        return 0

    if not args.confirm and not _confirm_execution():
        return 0

    return _execute_actions_or_exit(api, actions)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.debug)
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
