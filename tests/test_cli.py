"""Integration tests for cli.py pipeline."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from readwise_reader_filter.cli import (
    _collect_actions,
    _execute_actions,
    _fetch_entries,
    main,
)
from readwise_reader_filter.models import ReaderFeedEntry
from readwise_reader_filter.types import (
    AgeFilter,
    FeedDefinition,
    FilterConfig,
    FilterRule,
    SourceMatch,
    StringFilter,
)


def _entry(**overrides) -> ReaderFeedEntry:
    defaults = dict(
        id="1",
        title="Test Title",
        author="Test Author",
        source_url="https://example.com/article",
        site_name="Example",
        summary="Test summary text",
        location="feed",
        category="article",
        tags=["tag1", "tag2"],
        notes="",
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    defaults.update(overrides)
    return ReaderFeedEntry(**defaults)


# ── _fetch_entries ────────────────────────────────────────────────


class TestFetchEntries:
    def test_filters_feed_location(self):
        api = MagicMock()
        api.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "Feed entry"},
        ]
        entries = _fetch_entries(api, refresh=False)
        assert len(entries) == 1
        assert entries[0].id == "1"
        api.list_documents.assert_called_once_with(
            location="feed", force_refresh=False, with_html_content=False
        )

    def test_empty_result(self):
        api = MagicMock()
        api.list_documents.return_value = []
        entries = _fetch_entries(api, refresh=False)
        assert entries == []

    def test_passes_refresh_flag(self):
        api = MagicMock()
        api.list_documents.return_value = []
        _fetch_entries(api, refresh=True)
        api.list_documents.assert_called_once_with(
            location="feed", force_refresh=True, with_html_content=False
        )


# ── _collect_actions ────────────────────────────────────────────────


class TestCollectActions:
    def test_global_rule_matches(self):
        entries = [_entry(title="SPONSORED: Check this out")]
        config = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        display_results, actions = _collect_actions(entries, config)
        assert len(display_results) == 1
        assert len(actions) == 1
        assert actions[0][1].action == "delete"
        assert display_results[0][1][0].scope == "global"

    def test_feed_specific_rule_matches(self):
        entries = [
            _entry(
                source_url="https://www.youtube.com/shorts/abc",
                site_name="YouTube",
            )
        ]
        config = FilterConfig(
            feeds=[
                FeedDefinition(
                    name="YouTube",
                    match=SourceMatch(domains=["youtube.com"]),
                    rules=[
                        FilterRule(
                            name="Delete shorts",
                            url=StringFilter(contains=["/shorts/"]),
                            action="delete",
                        )
                    ],
                )
            ]
        )
        display_results, actions = _collect_actions(entries, config)
        assert len(actions) == 1
        assert display_results[0][1][0].scope == "YouTube"

    def test_no_match(self):
        entries = [_entry(title="Normal article")]
        config = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        display_results, actions = _collect_actions(entries, config)
        assert len(actions) == 0
        assert len(display_results) == 0

    def test_feed_rules_only_apply_to_matching_entries(self):
        entries = [
            _entry(
                id="1",
                source_url="https://www.youtube.com/shorts/abc",
                site_name="YouTube",
            ),
            _entry(
                id="2", source_url="https://example.com/article", site_name="Example"
            ),
        ]
        config = FilterConfig(
            feeds=[
                FeedDefinition(
                    name="YouTube",
                    match=SourceMatch(domains=["youtube.com"]),
                    rules=[
                        FilterRule(
                            name="Delete shorts",
                            url=StringFilter(contains=["/shorts/"]),
                            action="delete",
                        )
                    ],
                )
            ]
        )
        display_results, actions = _collect_actions(entries, config)
        assert len(actions) == 1
        assert actions[0][0].id == "1"

    def test_multiple_rules_all_returned(self):
        entries = [
            _entry(
                title="SPONSORED video",
                source_url="https://www.youtube.com/watch?v=123",
            )
        ]
        config = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Archive sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="archive",
                )
            ],
            feeds=[
                FeedDefinition(
                    name="YouTube",
                    match=SourceMatch(domains=["youtube.com"]),
                    rules=[
                        FilterRule(
                            name="Delete sponsored",
                            title=StringFilter(contains_any=["SPONSORED"]),
                            action="delete",
                        )
                    ],
                )
            ],
        )
        display_results, actions = _collect_actions(entries, config)
        assert len(display_results) == 1
        assert len(actions) == 2
        action_set = {r.action for _, r in actions}
        assert action_set == {"archive", "delete"}

    def test_age_based_rule(self):
        old_entry = _entry(
            id="old",
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        new_entry = _entry(
            id="new",
            created_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        config = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Archive old",
                    age=AgeFilter(older_than_days=14),
                    action="archive",
                )
            ]
        )
        display_results, actions = _collect_actions([old_entry, new_entry], config)
        assert len(actions) == 1
        assert actions[0][0].id == "old"

    def test_mark_all_as_unseen_adds_unseen_for_seen_entries(self):
        entries = [
            _entry(id="seen1", first_opened_at=datetime.now(timezone.utc) - timedelta(days=5)),
            _entry(id="new1", first_opened_at=None),
        ]
        config = FilterConfig(mark_all_as_unseen=True)
        display_results, actions = _collect_actions(entries, config)
        # Only seen entry gets unseen action
        assert len(actions) == 1
        assert actions[0][0].id == "seen1"
        assert actions[0][1].action == "unseen"
        assert actions[0][1].name == "mark_all_as_unseen"

    def test_mark_all_as_unseen_combines_with_rules(self):
        entries = [
            _entry(
                id="1",
                title="SPONSORED",
                first_opened_at=datetime.now(timezone.utc) - timedelta(days=5),
            )
        ]
        config = FilterConfig(
            mark_all_as_unseen=True,
            global_rules=[
                FilterRule(
                    name="Delete sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ],
        )
        display_results, actions = _collect_actions(entries, config)
        # Both delete and unseen actions
        assert len(actions) == 2
        action_set = {r.action for _, r in actions}
        assert action_set == {"delete", "unseen"}


# ── _execute_actions ──────────────────────────────────────────────


class TestExecuteActions:
    def test_delete_action(self):
        api = MagicMock()
        api.delete_document.return_value = True
        entry = _entry()
        actions = [(entry, FilterRule(name="test", action="delete"))]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0
        api.delete_document.assert_called_once_with(entry.id)

    def test_archive_uses_bulk_update(self):
        api = MagicMock()
        api.bulk_update.return_value = 2
        api.is_marked_for_delete.return_value = False
        actions = [
            (_entry(id="1"), FilterRule(name="r1", action="archive")),
            (_entry(id="2"), FilterRule(name="r2", action="archive")),
        ]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0
        api.bulk_update.assert_called_once()
        call_args = api.bulk_update.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]["location"] == "archive"
        assert call_args[1]["location"] == "archive"

    def test_seen_uses_bulk_update(self):
        api = MagicMock()
        api.bulk_update.return_value = 2
        api.is_marked_for_delete.return_value = False
        actions = [
            (_entry(id="1"), FilterRule(name="r1", action="seen")),
            (_entry(id="2"), FilterRule(name="r2", action="seen")),
        ]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0
        api.bulk_update.assert_called_once()
        call_args = api.bulk_update.call_args[0][0]
        assert call_args[0]["seen"] is True
        assert call_args[1]["seen"] is True

    def test_mark_for_delete_uses_bulk_update(self):
        api = MagicMock()
        api.bulk_update.return_value = 1
        api.is_marked_for_delete.return_value = False
        entry = _entry(id="1", tags=["existing"])
        rule = FilterRule(name="test rule", action="mark_for_delete")
        exit_code = _execute_actions(api, [(entry, rule)])
        assert exit_code == 0
        api.bulk_update.assert_called_once()
        call_args = api.bulk_update.call_args[0][0]
        assert call_args[0]["tags"] == ["existing", "delete"]
        assert call_args[0]["location"] == "later"
        assert call_args[0]["notes"] == "DELETE: test rule"

    def test_skips_already_marked_for_delete(self):
        api = MagicMock()
        api.bulk_update.return_value = 0
        api.is_marked_for_delete.return_value = True
        entry = _entry(id="1")
        rule = FilterRule(name="test", action="mark_for_delete")
        exit_code = _execute_actions(api, [(entry, rule)])
        assert exit_code == 0
        api.bulk_update.assert_not_called()

    def test_error_handling(self):
        api = MagicMock()
        api.delete_document.side_effect = Exception("API error")
        actions = [(_entry(), FilterRule(name="test", action="delete"))]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 1

    def test_logs_combined_actions_to_jsonl(self, tmp_path, monkeypatch):
        log_file = tmp_path / "actions.jsonl"
        monkeypatch.setattr(
            "readwise_reader_filter.cli.action_log_path", lambda: log_file
        )
        api = MagicMock()
        api.bulk_update.return_value = 1
        api.is_marked_for_delete.return_value = False
        raw_payload = {"id": "api-123", "title": "From API"}
        entry = ReaderFeedEntry.from_api(raw_payload)
        actions = [
            (entry, FilterRule(name="archive rule", action="archive")),
            (entry, FilterRule(name="unseen rule", action="unseen")),
        ]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert set(record["actions"]) == {"archive", "unseen"}
        assert set(record["rules"]) == {"archive rule", "unseen rule"}
        assert record["entry"] == raw_payload
        assert "performed_at" in record

    def test_logs_action_with_minimal_entry(self, tmp_path, monkeypatch):
        log_file = tmp_path / "actions.jsonl"
        monkeypatch.setattr(
            "readwise_reader_filter.cli.action_log_path", lambda: log_file
        )
        api = MagicMock()
        api.bulk_update.return_value = 1
        api.is_marked_for_delete.return_value = False
        raw = {"id": "manual-id", "title": "Manual entry"}
        entry = _entry(raw=raw)
        rule = FilterRule(name="test", action="seen")
        exit_code = _execute_actions(api, [(entry, rule)])
        assert exit_code == 0

        lines = log_file.read_text().strip().split("\n")
        record = json.loads(lines[-1])
        assert record["entry"] == raw

    def test_log_rotation(self, tmp_path, monkeypatch):
        from readwise_reader_filter.cli import MAX_ACTION_LOG_BYTES

        log_file = tmp_path / "actions.jsonl"
        monkeypatch.setattr(
            "readwise_reader_filter.cli.action_log_path", lambda: log_file
        )
        oversized = b"x" * (MAX_ACTION_LOG_BYTES + 1)
        log_file.write_bytes(oversized)

        api = MagicMock()
        api.bulk_update.return_value = 1
        api.is_marked_for_delete.return_value = False
        entry = _entry()
        rule = FilterRule(name="test", action="seen")
        exit_code = _execute_actions(api, [(entry, rule)])
        assert exit_code == 0

        rotated = log_file.with_suffix(".jsonl.1")
        assert rotated.exists()
        assert rotated.read_bytes() == oversized
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        record = json.loads(lines[0])
        assert record["actions"] == ["seen"]

    def test_mixed_actions_batched_correctly(self):
        api = MagicMock()
        api.bulk_update.return_value = 3
        api.is_marked_for_delete.return_value = False
        api.delete_document.return_value = True
        actions = [
            (_entry(id="1"), FilterRule(name="r1", action="seen")),
            (_entry(id="2"), FilterRule(name="r2", action="archive")),
            (_entry(id="3"), FilterRule(name="r3", action="unseen")),
            (_entry(id="4"), FilterRule(name="r4", action="delete")),
        ]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0
        # Bulk update should have 3 items (seen, archive, unseen)
        api.bulk_update.assert_called_once()
        bulk_args = api.bulk_update.call_args[0][0]
        assert len(bulk_args) == 3
        # Delete should be called individually
        api.delete_document.assert_called_once_with("4")

    def test_multiple_actions_same_entry_merged(self):
        api = MagicMock()
        api.bulk_update.return_value = 1
        api.is_marked_for_delete.return_value = False
        entry = _entry(id="1")
        actions = [
            (entry, FilterRule(name="r1", action="archive")),
            (entry, FilterRule(name="r2", action="unseen")),
        ]
        exit_code = _execute_actions(api, actions)
        assert exit_code == 0
        # Should merge into one update with both fields
        api.bulk_update.assert_called_once()
        call_args = api.bulk_update.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0]["location"] == "archive"
        assert call_args[0]["seen"] is False


# ── main() CLI entrypoint ───────────────────────────────────────────


class TestMain:
    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    @patch("readwise_reader_filter.cli._execute_actions")
    def test_dry_run_skips_execution(self, mock_execute, mock_load_config, mock_client):
        mock_load_config.return_value = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        mock_client.return_value.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "SPONSORED item"}
        ]

        with patch("sys.argv", ["readwise-reader-filter", "--dry-run"]):
            exit_code = main()

        assert exit_code == 0
        mock_execute.assert_not_called()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    def test_missing_config_returns_zero(self, mock_load_config, mock_client):
        mock_load_config.return_value = FilterConfig()

        with patch("sys.argv", ["readwise-reader-filter", "--dry-run"]):
            exit_code = main()

        assert exit_code == 0
        mock_client.assert_not_called()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    def test_missing_config_without_dry_run_returns_zero(
        self, mock_load_config, mock_client
    ):
        mock_load_config.return_value = FilterConfig()

        with patch("sys.argv", ["readwise-reader-filter"]):
            exit_code = main()

        assert exit_code == 0
        mock_client.assert_not_called()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    @patch("readwise_reader_filter.cli._execute_actions")
    def test_execute_runs_when_not_dry_run_with_confirm(
        self, mock_execute, mock_load_config, mock_client
    ):
        mock_load_config.return_value = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        mock_client.return_value.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "SPONSORED item"}
        ]
        mock_execute.return_value = 0

        with patch("sys.argv", ["readwise-reader-filter", "--confirm"]):
            exit_code = main()

        assert exit_code == 0
        mock_execute.assert_called_once()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    @patch("readwise_reader_filter.cli._execute_actions")
    def test_no_confirm_aborts_on_no(self, mock_execute, mock_load_config, mock_client):
        mock_load_config.return_value = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        mock_client.return_value.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "SPONSORED item"}
        ]

        with (
            patch("sys.argv", ["readwise-reader-filter"]),
            patch("builtins.input", return_value="n"),
        ):
            exit_code = main()

        assert exit_code == 0
        mock_execute.assert_not_called()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    @patch("readwise_reader_filter.cli._execute_actions")
    def test_confirm_flag_skips_prompt(
        self, mock_execute, mock_load_config, mock_client
    ):
        mock_load_config.return_value = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        mock_client.return_value.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "SPONSORED item"}
        ]
        mock_execute.return_value = 0

        with patch("sys.argv", ["readwise-reader-filter", "--confirm"]):
            exit_code = main()

        assert exit_code == 0
        mock_execute.assert_called_once()

    @patch("readwise_reader_filter.cli.ReadwiseReaderAPIClient")
    @patch("readwise_reader_filter.cli.load_config")
    @patch("readwise_reader_filter.cli._execute_actions")
    def test_refresh_passes_to_list_documents(
        self, mock_execute, mock_load_config, mock_client
    ):
        mock_load_config.return_value = FilterConfig(
            global_rules=[
                FilterRule(
                    name="Block sponsored",
                    title=StringFilter(contains_any=["SPONSORED"]),
                    action="delete",
                )
            ]
        )
        mock_client.return_value.list_documents.return_value = [
            {"id": "1", "location": "feed", "title": "SPONSORED item"}
        ]
        mock_execute.return_value = 0

        with patch("sys.argv", ["readwise-reader-filter", "--confirm", "--refresh"]):
            exit_code = main()

        assert exit_code == 0
        mock_client.return_value.list_documents.assert_called_once_with(
            location="feed", force_refresh=True, with_html_content=False
        )

    @patch("readwise_reader_filter.cli.validate_config")
    def test_validate_exits_clean(self, mock_validate):
        mock_validate.return_value = []

        with patch("sys.argv", ["readwise-reader-filter", "--validate"]):
            exit_code = main()

        assert exit_code == 0

    @patch("readwise_reader_filter.cli.validate_config")
    def test_validate_exits_with_errors(self, mock_validate):
        mock_validate.return_value = ["unknown key 'typo'"]

        with patch("sys.argv", ["readwise-reader-filter", "--validate"]):
            exit_code = main()

        assert exit_code == 1
