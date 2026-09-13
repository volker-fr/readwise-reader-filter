"""Unit tests for parsing.py — YAML loading, validation, and coercion."""

import logging

import pytest

from readwise_reader_filter.parsing import (
    FilterConfigError,
    _coerce_rule_dict,
    _from_dict,
    _validate_config,
    load_config,
    validate_config,
)
from readwise_reader_filter.types import (
    AgeFilter,
    StringFilter,
)

# ── _coerce_rule_dict ─────────────────────────────────────────────


class TestCoerceRuleDict:
    def test_string_filter_from_dict(self):
        raw = {"title": {"contains": ["foo"]}, "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert isinstance(coerced["title"], StringFilter)
        assert coerced["title"].contains == ["foo"]

    def test_age_from_dict(self):
        raw = {"age": {"older_than_days": 7}, "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert isinstance(coerced["age"], AgeFilter)
        assert coerced["age"].older_than_days == 7

    def test_tags_include_string_normalization(self):
        raw = {"tags_include": "python", "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert coerced["tags_include"] == ["python"]

    def test_tags_include_list_normalization(self):
        raw = {"tags_include": ["python", None, "", "rust"], "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert coerced["tags_include"] == ["python", "rust"]

    def test_tags_include_empty_list_becomes_none(self):
        raw = {"tags_include": [], "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert coerced["tags_include"] is None

    def test_invalid_tags_type_raises(self):
        raw = {"tags_include": 123, "action": "archive"}
        with pytest.raises(TypeError):
            _coerce_rule_dict(raw)

    def test_string_title_coercion(self):
        raw = {"title": "foo", "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert isinstance(coerced["title"], StringFilter)
        assert coerced["title"].contains == ["foo"]

    def test_string_summary_coercion(self):
        raw = {"summary": "bar", "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert isinstance(coerced["summary"], StringFilter)
        assert coerced["summary"].contains == ["bar"]

    def test_string_url_coercion(self):
        raw = {"url": "https://example.com/", "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert isinstance(coerced["url"], StringFilter)
        assert coerced["url"].contains == ["https://example.com/"]

    def test_empty_string_title_becomes_none(self):
        raw = {"title": "", "action": "archive"}
        coerced = _coerce_rule_dict(raw)
        assert coerced["title"] is None


# ── load_config ───────────────────────────────────────────────────


class TestLoadConfig:
    def test_valid_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
global:
  - name: "Block spam"
    title:
      contains_any: ["SPAM"]
    action: delete

feeds:
  - name: "YouTube"
    match:
      domains: ["youtube.com"]
    rules:
      - name: "Delete shorts"
        url:
          contains: ["/shorts/"]
        action: delete
""")
        config = load_config(config_file)
        assert len(config.global_rules) == 1
        assert config.global_rules[0].name == "Block spam"
        assert len(config.feeds) == 1
        assert config.feeds[0].name == "YouTube"
        assert len(config.feeds[0].rules) == 1

    def test_missing_file_raises_config_error(self, tmp_path):
        with pytest.raises(FilterConfigError, match="Config file not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises_config_error(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{{invalid yaml}}")
        with pytest.raises(FilterConfigError, match="Error reading config file"):
            load_config(config_file)

    def test_invalid_rule_raises_config_error(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
global:
  - name: "Bad rule"
    action: invalid_action
""")
        with pytest.raises(FilterConfigError, match="invalid action 'invalid_action'"):
            load_config(config_file)

    def test_null_rules_field(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
global: null
feeds: null
""")
        config = load_config(config_file)
        assert config.global_rules == []
        assert config.feeds == []


# ── _validate_config ──────────────────────────────────────────────


class TestValidateConfig:
    def test_valid_config(self):
        data = {
            "global": [
                {"name": "test", "title": {"contains": ["foo"]}, "action": "delete"}
            ],
            "feeds": [
                {
                    "name": "My Feed",
                    "match": {"domains": ["example.com"]},
                    "rules": [
                        {
                            "name": "rule1",
                            "age": {"older_than_days": 7},
                            "action": "archive",
                        }
                    ],
                }
            ],
        }
        assert _validate_config(data) == []

    def test_unknown_top_level_key(self):
        data = {"global": [], "unknown_key": "oops"}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown top-level key 'unknown_key'" in errors[0]

    def test_unknown_rule_key(self):
        data = {"global": [{"name": "test", "typo_field": "bar", "action": "archive"}]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown key 'typo_field'" in errors[0]

    def test_unknown_feed_key(self):
        data = {"feeds": [{"name": "test", "match": {}, "rules": [], "badkey": 1}]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown key 'badkey'" in errors[0]

    def test_unknown_string_filter_key(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "title": {"contains": ["foo"], "badsub": "bar"},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown key 'badsub'" in errors[0]

    def test_unknown_source_match_key(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {"authors": ["Alice"], "badfield": True},
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown key 'badfield'" in errors[0]

    def test_unknown_age_filter_key(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "age": {"older_than_days": 7, "wrong": True},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "unknown key 'wrong'" in errors[0]

    def test_invalid_regex_in_matches(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "title": {"matches": ["[invalid"]},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "invalid regex" in errors[0]

    def test_age_older_than_days_must_be_positive_integer(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "age": {"older_than_days": 0},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "older_than_days' must be >= 1" in errors[0]

    def test_age_older_than_days_rejects_string(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "age": {"older_than_days": "seven"},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "older_than_days' must be a positive integer" in errors[0]

    def test_valid_regex_in_matches(self):
        data = {
            "global": [
                {
                    "name": "test",
                    "title": {"matches": [r"\d+", r"(?i)foo"]},
                    "action": "archive",
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 0

    def test_multiple_errors_collected(self):
        data = {
            "global": [
                {
                    "name": "bad",
                    "typo_field": "x",
                    "title": {"badsub": "y"},
                    "action": "invalid_action",
                }
            ],
            "unknown_top": True,
        }
        errors = _validate_config(data)
        assert len(errors) >= 3

    def test_rule_no_filter_fields_warns(self, caplog):
        data = {"global": [{"name": "empty rule", "action": "archive"}]}
        with caplog.at_level(logging.WARNING, logger="readwise_reader_filter.parsing"):
            errors = _validate_config(data)
        assert len(errors) == 0
        assert any("no filter fields" in r.message for r in caplog.records)

    def test_feed_empty_rules_warns(self):
        data = {"feeds": [{"name": "My Feed", "rules": []}]}
        errors = _validate_config(data)
        assert len(errors) == 0

    def test_global_not_list(self):
        data = {"global": "oops"}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must be a list" in errors[0]

    def test_feeds_not_list(self):
        data = {"feeds": "oops"}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must be a list" in errors[0]

    def test_feed_match_not_dict(self):
        data = {"feeds": [{"name": "test", "match": "bad", "rules": []}]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must be a dict" in errors[0]

    def test_rule_not_dict(self):
        data = {"global": ["bad rule"]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must be a dict" in errors[0]

    def test_feed_not_dict(self):
        data = {"feeds": ["bad feed"]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must be a dict" in errors[0]

    def test_invalid_action_in_rule(self):
        data = {
            "global": [{"name": "test", "title": {"contains": ["x"]}, "action": "move"}]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "invalid action 'move'" in errors[0]

    def test_title_not_string_or_dict(self):
        data = {"global": [{"name": "test", "title": 123, "action": "archive"}]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "'title' must be a string or dict" in errors[0]

    def test_feed_rules_not_list(self):
        data = {"feeds": [{"name": "test", "rules": "oops"}]}
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "'rules' must be a list" in errors[0]

    def test_source_match_field_not_list(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {"site_names": "YouTube"},
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "'site_names' must be a list" in errors[0]

    def test_source_match_field_contains_non_string(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {"authors": ["Alice", 123]},
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "must contain only strings" in errors[0]

    def test_source_match_string_filter_form(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {
                        "site_names": {"contains": ["tube"]},
                        "authors": {"matches": [r"^alice"]},
                        "categories": {"matches": [r"^rss$"], "exclude_with": ["spam"]},
                    },
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert errors == []

    def test_source_match_string_filter_invalid_regex(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {"authors": {"matches": ["[invalid"]}},
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "invalid regex" in errors[0]

    def test_source_match_field_bad_type_message(self):
        data = {
            "feeds": [
                {
                    "name": "test",
                    "match": {"site_names": 42},
                    "rules": [],
                }
            ]
        }
        errors = _validate_config(data)
        assert len(errors) == 1
        assert "'site_names' must be a list, dict, or omitted" in errors[0]


# ── validate_config (public API) ──────────────────────────────────


class TestValidateConfigPublic:
    def test_valid_file(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
global:
  - name: "test"
    title:
      contains: ["foo"]
    action: delete
""")
        errors = validate_config(config_file)
        assert errors == []

    def test_missing_file(self, tmp_path):
        with pytest.raises(FilterConfigError, match="not found"):
            validate_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("{{bad yaml")
        with pytest.raises(FilterConfigError, match="Error reading config file"):
            validate_config(config_file)

    def test_errors_returned(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
global:
  - name: "bad"
    typo_field: "oops"
    action: archive
""")
        errors = validate_config(config_file)
        assert len(errors) == 1
        assert "typo_field" in errors[0]


# ── _from_dict ───────────────────────────────────────────────────


class TestFromDict:
    def test_empty_config(self):
        config = _from_dict({})
        assert config.global_rules == []
        assert config.feeds == []

    def test_global_rules(self):
        data = {
            "global": [
                {"name": "rule1", "title": {"contains": ["foo"]}, "action": "delete"}
            ]
        }
        config = _from_dict(data)
        assert len(config.global_rules) == 1
        assert config.global_rules[0].name == "rule1"

    def test_feeds_with_match(self):
        data = {
            "feeds": [
                {
                    "name": "YouTube",
                    "match": {"domains": ["youtube.com"]},
                    "rules": [
                        {
                            "name": "rule1",
                            "url": {"contains": ["/shorts/"]},
                            "action": "delete",
                        }
                    ],
                }
            ]
        }
        config = _from_dict(data)
        assert len(config.feeds) == 1
        assert config.feeds[0].name == "YouTube"
        assert config.feeds[0].match is not None
        assert len(config.feeds[0].rules) == 1

    def test_feed_match_string_filter_form(self):
        data = {
            "feeds": [
                {
                    "name": "DMN",
                    "match": {
                        "site_names": {"contains": ["news"]},
                        "authors": {"matches": [r"^example"]},
                    },
                    "rules": [{"name": "rule1", "action": "archive"}],
                }
            ]
        }
        config = _from_dict(data)
        match = config.feeds[0].match
        assert match.authors is None
        assert match.authors_filter is not None
        assert match.authors_filter.matches == [r"^example"]
        assert match.site_names is None
        assert match.site_names_filter is not None

    def test_feed_without_match(self):
        data = {
            "feeds": [
                {
                    "name": "All",
                    "rules": [{"name": "rule1", "action": "archive"}],
                }
            ]
        }
        config = _from_dict(data)
        assert config.feeds[0].match is None

    def test_null_global_and_feeds(self):
        config = _from_dict({"global": None, "feeds": None})
        assert config.global_rules == []
        assert config.feeds == []

    def test_global_not_list_raises(self):
        with pytest.raises(TypeError, match="must be a list"):
            _from_dict({"global": "oops"})

    def test_feeds_not_list_raises(self):
        with pytest.raises(TypeError, match="must be a list"):
            _from_dict({"feeds": "oops"})
