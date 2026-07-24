"""Unit tests for types.py — data model types and utilities."""

import pytest

from readwise_reader_filter.types import (
    AgeFilter,
    FilterRule,
    SourceMatch,
    StringFilter,
    _coerce_str_list,
    _normalize_str_list_fields,
)

# ── _coerce_str_list ─────────────────────────────────────────────


class TestCoerceStrList:
    def test_string_to_list(self):
        assert _coerce_str_list("hello") == ["hello"]

    def test_empty_string_to_none(self):
        assert _coerce_str_list("") is None

    def test_list_filters_none_and_empty(self):
        assert _coerce_str_list(["a", None, "", "b"]) == ["a", "b"]

    def test_empty_list_to_none(self):
        assert _coerce_str_list([]) is None

    def test_none_stays_none(self):
        assert _coerce_str_list(None) is None

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="must be a string or list"):
            _coerce_str_list(123)

    def test_list_coerces_ints_to_strings(self):
        assert _coerce_str_list([1, 2, 3]) == ["1", "2", "3"]


# ── _normalize_str_list_fields ───────────────────────────────────


class TestNormalizeStrListFields:
    def test_string_to_list(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags="hello")
        _normalize_str_list_fields(obj, ("tags",))
        assert obj.tags == ["hello"]

    def test_empty_string_to_none(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags="")
        _normalize_str_list_fields(obj, ("tags",))
        assert obj.tags is None

    def test_list_filters_none_and_empty(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags=["a", None, "", "b"])
        _normalize_str_list_fields(obj, ("tags",))
        assert obj.tags == ["a", "b"]

    def test_empty_list_to_none(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags=[])
        _normalize_str_list_fields(obj, ("tags",))
        assert obj.tags is None

    def test_none_stays_none(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags=None)
        _normalize_str_list_fields(obj, ("tags",))
        assert obj.tags is None

    def test_invalid_type_raises(self):
        from dataclasses import dataclass

        @dataclass
        class Obj:
            tags: list[str] | None = None

        obj = Obj(tags=123)
        with pytest.raises(TypeError, match="must be a string or list"):
            _normalize_str_list_fields(obj, ("tags",))


# ── StringFilter.__post_init__ ───────────────────────────────────


class TestStringFilterPostInit:
    def test_compiles_valid_regex(self):
        sf = StringFilter(matches=[r"\d+", r"(?i)foo"])
        assert len(sf._compiled_matches) == 2
        assert all(c is not None for c in sf._compiled_matches)

    def test_invalid_regex_compiles_to_none(self):
        sf = StringFilter(matches=[r"[invalid"])
        assert len(sf._compiled_matches) == 1
        assert sf._compiled_matches[0] is None

    def test_mixed_valid_invalid_regex(self):
        sf = StringFilter(matches=[r"\d+", r"[invalid"])
        assert sf._compiled_matches[0] is not None
        assert sf._compiled_matches[1] is None

    def test_empty_matches_list(self):
        sf = StringFilter(matches=[])
        assert sf._compiled_matches == []

    def test_none_matches_list(self):
        sf = StringFilter(matches=None)
        assert sf._compiled_matches == []


# ── AgeFilter.__post_init__ ──────────────────────────────────────


class TestAgeFilterPostInit:
    def test_valid_age(self):
        af = AgeFilter(older_than_days=7)
        assert af.older_than_days == 7

    def test_age_zero_raises(self):
        with pytest.raises(ValueError, match="older_than_days must be >= 1"):
            AgeFilter(older_than_days=0)

    def test_negative_age_raises(self):
        with pytest.raises(ValueError, match="older_than_days must be >= 1"):
            AgeFilter(older_than_days=-1)

    def test_none_age_is_valid(self):
        af = AgeFilter(older_than_days=None)
        assert af.older_than_days is None


# ── FilterRule.__post_init__ ─────────────────────────────────────


class TestFilterRulePostInit:
    def test_valid_action(self):
        rule = FilterRule(action="archive")
        assert rule.action == "archive"

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError, match="Invalid action 'move'"):
            FilterRule(action="move")

    def test_default_action_is_archive(self):
        rule = FilterRule()
        assert rule.action == "archive"


# ── SourceMatch.__post_init__ ────────────────────────────────────


class TestSourceMatchPostInit:
    def test_normalizes_string_to_list(self):
        sm = SourceMatch(site_names="YouTube")
        assert sm.site_names == ["YouTube"]

    def test_normalizes_list(self):
        sm = SourceMatch(authors=["Alice", None, "", "Bob"])
        assert sm.authors == ["Alice", "Bob"]

    def test_none_fields_stay_none(self):
        sm = SourceMatch()
        assert sm.site_names is None
        assert sm.authors is None
