"""Data model types and constants for feed entry filtering."""

import re
from dataclasses import dataclass, field

ACTION_PRECEDENCE = {
    "delete": 3,
    "mark_for_delete": 2,
    "archive": 1,
    "seen": 0,
    "unseen": 0,
}

VALID_ACTIONS = frozenset(
    {"delete", "mark_for_delete", "archive", "seen", "unseen"}
)


class FilterConfigError(Exception):
    """Raised when the filter config file has parse or validation errors."""


def _coerce_str_list(val):
    """Normalize a str/list value: str→list, filter None/empty, empty→None."""
    if isinstance(val, str):
        return [val] if val else None
    if isinstance(val, list):
        coerced = [str(v) for v in val if v is not None and str(v) != ""]
        return coerced if coerced else None
    if val is not None:
        raise TypeError(f"must be a string or list, got {type(val).__name__}")
    return None


def _normalize_str_list_fields(obj: object, field_names: tuple[str, ...]) -> None:
    """Normalize string/list fields on a dataclass instance."""
    for fld in field_names:
        setattr(obj, fld, _coerce_str_list(getattr(obj, fld)))


@dataclass
class StringFilter:
    """Filters applied to a text field (title, summary, url).

    All patterns are checked case-insensitively. Substring checks use a
    lowercased target; regex `matches` patterns are compiled with the
    IGNORECASE flag. Inside a single StringFilter, ALL sub-filters must
    pass for the overall check to pass.

    Fields:
        contains:      entry MUST contain ALL of these substrings
        contains_any:  entry MUST contain AT LEAST ONE of these substrings
        matches:       entry MUST match ALL of these regex patterns
        exclude_with:  entry MUST NOT contain ANY of these substrings
    """

    contains: list[str] | None = None
    contains_any: list[str] | None = None
    matches: list[str] | None = None
    exclude_with: list[str] | None = None
    _compiled_matches: list[re.Pattern | None] = field(
        default_factory=list, repr=False
    )

    def __post_init__(self):
        _normalize_str_list_fields(
            self, ("contains", "contains_any", "matches", "exclude_with")
        )
        self._compiled_matches = []
        if self.matches:
            for pat in self.matches:
                try:
                    self._compiled_matches.append(
                        re.compile(pat, re.IGNORECASE)
                    )
                except re.error:
                    self._compiled_matches.append(None)


@dataclass
class SourceMatch:
    """Criteria to identify which feed an entry belongs to.

    If all fields are None (empty match), it matches every entry.
    If any field is set, ALL set fields must match for the entry to belong.
    """

    site_names: list[str] | None = None
    authors: list[str] | None = None
    domains: list[str] | None = None
    categories: list[str] | None = None

    def __post_init__(self):
        _normalize_str_list_fields(
            self, ("site_names", "authors", "domains", "categories")
        )


@dataclass
class AgeFilter:
    older_than_days: int | None = None

    def __post_init__(self):
        if self.older_than_days is not None and self.older_than_days < 1:
            raise ValueError(
                f"older_than_days must be >= 1, got {self.older_than_days}"
            )


@dataclass
class FilterRule:
    name: str = ""
    title: StringFilter | None = None
    summary: StringFilter | None = None
    content: StringFilter | None = None
    url: StringFilter | None = None
    tags_include: list[str] | None = None
    tags_exclude: list[str] | None = None
    age: AgeFilter | None = None
    read: bool | None = None
    action: str = "archive"

    def __post_init__(self):
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"Invalid action '{self.action}' in rule '{self.name}'. "
                f"Valid: {', '.join(sorted(VALID_ACTIONS))}"
            )


@dataclass
class FeedDefinition:
    name: str = ""
    match: SourceMatch | None = None
    rules: list[FilterRule] = field(default_factory=list)


@dataclass
class MatchResult:
    scope: str
    rule: FilterRule
    reasons: list[str]


@dataclass
class FilterConfig:
    global_rules: list[FilterRule] = field(default_factory=list)
    feeds: list[FeedDefinition] = field(default_factory=list)
    mark_all_as_unseen: bool = False
