"""Unit tests for matching.py — entry matching and action resolution."""

from datetime import datetime, timedelta, timezone

import pytest

from readwise_reader_filter.matching import (
    check_string_filter,
    entry_matches_rule,
    entry_matches_source,
    resolve_action,
)
from readwise_reader_filter.models import ReaderFeedEntry
from readwise_reader_filter.types import (
    AgeFilter,
    FilterRule,
    MatchResult,
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
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ReaderFeedEntry(**defaults)


# ── check_string_filter ──────────────────────────────────────────


class TestStringFilterContains:
    def test_all_patterns_must_match(self):
        sf = StringFilter(contains=["foo", "bar"])
        ok, _ = check_string_filter("foo bar baz", sf)
        assert ok
        assert not check_string_filter("foo baz", sf)[0]
        assert not check_string_filter("bar baz", sf)[0]

    def test_case_insensitive(self):
        sf = StringFilter(contains=["FOO"])
        ok, _ = check_string_filter("foo bar", sf)
        assert ok

    def test_empty_list_matches_everything(self):
        sf = StringFilter(contains=[])
        ok, _ = check_string_filter("anything", sf)
        assert ok


class TestStringFilterContainsAny:
    def test_at_least_one_must_match(self):
        sf = StringFilter(contains_any=["foo", "bar"])
        ok, _ = check_string_filter("foo baz", sf)
        assert ok
        ok, _ = check_string_filter("bar baz", sf)
        assert ok
        assert not check_string_filter("baz qux", sf)[0]

    def test_case_insensitive(self):
        sf = StringFilter(contains_any=["FOO"])
        ok, _ = check_string_filter("foo", sf)
        assert ok


class TestStringFilterMatches:
    def test_regex_must_match(self):
        sf = StringFilter(matches=[r"foo\d+"])
        ok, _ = check_string_filter("foo123", sf)
        assert ok
        assert not check_string_filter("foobar", sf)[0]

    def test_invalid_regex_fails(self):
        sf = StringFilter(matches=[r"[invalid"])
        ok, reasons = check_string_filter("anything", sf)
        assert not ok
        assert "invalid regex" in reasons[0]

    def test_case_insensitive_regex(self):
        sf = StringFilter(matches=[r"foo"])
        ok, _ = check_string_filter("FOO bar", sf)
        assert ok

    def test_uppercase_pattern_matches_lowercase_text(self):
        sf = StringFilter(matches=[r"FOO"])
        ok, _ = check_string_filter("foo bar", sf)
        assert ok

    def test_case_control_in_pattern_is_redundant(self):
        sf = StringFilter(matches=[r"(?i)FOO"])
        ok, _ = check_string_filter("foo bar", sf)
        assert ok


class TestStringFilterExcludeWith:
    def test_excludes_if_any_match(self):
        sf = StringFilter(exclude_with=["spam", "ad"])
        assert not check_string_filter("this is spam", sf)[0]
        ok, _ = check_string_filter("this is clean", sf)
        assert ok

    def test_case_insensitive(self):
        sf = StringFilter(exclude_with=["SPAM"])
        assert not check_string_filter("this is spam", sf)[0]


class TestStringFilterCombined:
    def test_all_subfilters_must_pass(self):
        sf = StringFilter(contains=["hello"], exclude_with=["world"])
        ok, _ = check_string_filter("hello there", sf)
        assert ok
        assert not check_string_filter("hello world", sf)[0]
        assert not check_string_filter("goodbye there", sf)[0]


# ── entry_matches_source ─────────────────────────────────────────


class TestSourceMatch:
    def test_none_matches_everything(self):
        entry = _entry()
        ok, _ = entry_matches_source(entry, None)
        assert ok

    def test_empty_match_matches_everything(self):
        entry = _entry()
        ok, _ = entry_matches_source(entry, SourceMatch())
        assert ok

    def test_site_names(self):
        entry = _entry(site_name="YouTube")
        ok, _ = entry_matches_source(entry, SourceMatch(site_names=["YouTube"]))
        assert ok
        assert not entry_matches_source(entry, SourceMatch(site_names=["Twitter"]))[0]

    def test_authors(self):
        entry = _entry(author="Alice")
        ok, _ = entry_matches_source(entry, SourceMatch(authors=["Alice"]))
        assert ok
        assert not entry_matches_source(entry, SourceMatch(authors=["Bob"]))[0]

    def test_domains(self):
        entry = _entry(source_url="https://www.youtube.com/watch?v=123")
        ok, _ = entry_matches_source(
            entry, SourceMatch(domains=["youtube.com"])
        )
        assert ok
        assert not entry_matches_source(
            entry, SourceMatch(domains=["twitter.com"])
        )[0]

    def test_categories(self):
        entry = _entry(category="rss")
        ok, _ = entry_matches_source(entry, SourceMatch(categories=["rss"]))
        assert ok
        assert not entry_matches_source(entry, SourceMatch(categories=["email"]))[0]

    def test_all_set_fields_must_match(self):
        entry = _entry(site_name="YouTube", author="Alice")
        ok, _ = entry_matches_source(
            entry, SourceMatch(site_names=["YouTube"], authors=["Alice"])
        )
        assert ok
        assert not entry_matches_source(
            entry, SourceMatch(site_names=["YouTube"], authors=["Bob"])
        )[0]


# ── entry_matches_rule ────────────────────────────────────────────


class TestEntryMatchesRule:
    def test_no_filters_matches_everything(self):
        entry = _entry()
        rule = FilterRule(action="archive")
        ok, _ = entry_matches_rule(entry, rule)
        assert ok

    def test_title_filter(self):
        entry = _entry(title="Breaking: AI News")
        rule = FilterRule(title=StringFilter(contains=["breaking"]), action="archive")
        ok, _ = entry_matches_rule(entry, rule)
        assert ok
        assert not entry_matches_rule(
            entry, FilterRule(title=StringFilter(contains=["sports"]))
        )[0]

    def test_summary_filter(self):
        entry = _entry(summary="This is about machine learning")
        rule = FilterRule(
            summary=StringFilter(contains=["machine learning"]), action="archive"
        )
        ok, _ = entry_matches_rule(entry, rule)
        assert ok

    def test_url_filter(self):
        entry = _entry(source_url="https://www.youtube.com/shorts/abc123")
        rule = FilterRule(url=StringFilter(contains=["/shorts/"]), action="delete")
        ok, _ = entry_matches_rule(entry, rule)
        assert ok

    def test_tags_include(self):
        entry = _entry(tags=["python", "tutorial"])
        rule = FilterRule(tags_include=["python"], action="archive")
        ok, _ = entry_matches_rule(entry, rule)
        assert ok
        assert not entry_matches_rule(entry, FilterRule(tags_include=["rust"]))[0]

    def test_tags_exclude(self):
        entry = _entry(tags=["python", "tutorial"])
        rule = FilterRule(tags_exclude=["tutorial"], action="archive")
        assert not entry_matches_rule(entry, rule)[0]
        ok, _ = entry_matches_rule(entry, FilterRule(tags_exclude=["rust"]))
        assert ok

    def test_age_filter(self):
        old_entry = _entry(created_at=datetime.now(timezone.utc) - timedelta(days=30))
        new_entry = _entry(created_at=datetime.now(timezone.utc) - timedelta(days=2))
        rule = FilterRule(age=AgeFilter(older_than_days=14), action="archive")
        ok, _ = entry_matches_rule(old_entry, rule)
        assert ok
        assert not entry_matches_rule(new_entry, rule)[0]

    def test_age_filter_no_created_at(self):
        entry = _entry(created_at=None)
        rule = FilterRule(age=AgeFilter(older_than_days=7), action="archive")
        ok, _ = entry_matches_rule(entry, rule)
        assert not ok

    def test_all_filters_must_pass(self):
        entry = _entry(
            title="Breaking News",
            tags=["news"],
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        rule = FilterRule(
            title=StringFilter(contains=["breaking"]),
            tags_include=["news"],
            age=AgeFilter(older_than_days=7),
            action="archive",
        )
        ok, _ = entry_matches_rule(entry, rule)
        assert ok

        # fails because tag is wrong
        bad_entry = _entry(
            title="Breaking News",
            tags=["sports"],
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        assert not entry_matches_rule(bad_entry, rule)[0]


# ── resolve_action ────────────────────────────────────────────────


class TestResolveAction:
    def _match(self, action: str, scope: str = "global") -> MatchResult:
        return MatchResult(
            scope=scope,
            rule=FilterRule(action=action, name=f"{action}_{scope}"),
            reasons=[],
        )

    def test_single_match(self):
        m = self._match("archive")
        result = resolve_action([m])
        assert result.rule.action == "archive"

    def test_action_precedence(self):
        results = resolve_action(
            [
                self._match("archive"),
                self._match("delete"),
                self._match("mark_for_delete"),
            ]
        )
        assert results.rule.action == "delete"

    def test_feed_overrides_global(self):
        results = resolve_action(
            [
                self._match("archive", "global"),
                self._match("archive", "my_feed"),
            ]
        )
        assert results.scope == "my_feed"

    def test_higher_action_wins_over_scope(self):
        results = resolve_action(
            [
                self._match("archive", "my_feed"),
                self._match("delete", "global"),
            ]
        )
        assert results.rule.action == "delete"
        assert results.scope == "global"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            resolve_action([])

    def test_three_matches_highest_precedence_wins(self):
        results = resolve_action(
            [
                self._match("archive", "global"),
                self._match("mark_for_delete", "my_feed"),
                self._match("delete", "other_feed"),
            ]
        )
        assert results.rule.action == "delete"

    def test_includes_overridden_reasons(self):
        results = resolve_action(
            [
                self._match("archive", "global"),
                self._match("delete", "my_feed"),
            ]
        )
        assert any("overridden" in r for r in results.reasons)


# ── entry_matches_source domains case sensitivity ──────────────────


class TestSourceMatchDomainsCase:
    def test_case_insensitive_domain_match(self):
        entry = _entry(source_url="https://www.youtube.com/watch?v=123")
        ok, reasons = entry_matches_source(
            entry, SourceMatch(domains=["youtube.com"])
        )
        assert ok

    def test_uppercase_domain_matches_lowercase_url(self):
        entry = _entry(source_url="https://www.youtube.com/watch?v=123")
        ok, reasons = entry_matches_source(
            entry, SourceMatch(domains=["YOUTUBE.COM"])
        )
        assert ok

    def test_debug_reasons_show_original_domain(self):
        entry = _entry(source_url="https://www.youtube.com/watch?v=123")
        ok, reasons = entry_matches_source(
            entry, SourceMatch(domains=["YOUTUBE.COM"])
        )
        assert ok
        assert any("YOUTUBE.COM" in r for r in reasons)

    def test_subdomain_matches(self):
        entry = _entry(source_url="https://m.youtube.com/watch?v=123")
        ok, reasons = entry_matches_source(
            entry, SourceMatch(domains=["youtube.com"])
        )
        assert ok

    def test_www_stripped(self):
        entry = _entry(source_url="https://www.youtube.com/watch?v=123")
        ok, reasons = entry_matches_source(
            entry, SourceMatch(domains=["youtube.com"])
        )
        assert ok


# ── check_string_filter edge cases ────────────────────────────────


class TestStringFilterEdgeCases:
    def test_empty_string_filter_matches_everything(self):
        sf = StringFilter()
        ok, _ = check_string_filter("anything", sf)
        assert ok

    def test_none_fields_dont_block(self):
        sf = StringFilter(
            contains=None, contains_any=None, matches=None, exclude_with=None
        )
        ok, _ = check_string_filter("anything", sf)
        assert ok
