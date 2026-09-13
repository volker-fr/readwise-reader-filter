"""Entry matching against filter rules and action resolution."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import ReaderFeedEntry
from .types import (
    ACTION_PRECEDENCE,
    FilterRule,
    MatchResult,
    SourceMatch,
    StringFilter,
)


@dataclass(frozen=True)
class _StringCheck:
    """Descriptor for one sub-filter inside a StringFilter."""

    name: str
    is_active: Callable[[StringFilter], bool]
    run: Callable[[str, StringFilter, str], tuple[bool, list[str]]]


def _check_contains(
    text: str, sf: StringFilter, field_name: str
) -> tuple[bool, list[str]]:
    missing = [p for p in sf.contains if p.lower() not in text]
    if missing:
        return False, [f"{field_name}.contains missing: {missing}"]
    return True, [f"{field_name}.contains matched: {sf.contains}"]


def _check_contains_any(
    text: str, sf: StringFilter, field_name: str
) -> tuple[bool, list[str]]:
    if not any(p.lower() in text for p in sf.contains_any):
        return False, [f"{field_name}.contains_any none matched: {sf.contains_any}"]
    matched = [p for p in sf.contains_any if p.lower() in text]
    return True, [f"{field_name}.contains_any matched: {matched}"]


def _check_matches(
    text: str, sf: StringFilter, field_name: str
) -> tuple[bool, list[str]]:
    matched_pats = []
    for i, compiled in enumerate(sf._compiled_matches):
        if compiled is None:
            return False, [f"{field_name}.matches invalid regex: {sf.matches[i]!r}"]
        if compiled.search(text):
            matched_pats.append(sf.matches[i])
    if len(matched_pats) != len(sf.matches):
        return False, [
            f"{field_name}.matches not all matched: "
            f"{len(matched_pats)}/{len(sf.matches)} patterns matched"
        ]
    return True, [f"{field_name}.matches matched: {matched_pats}"]


def _check_exclude_with(
    text: str, sf: StringFilter, field_name: str
) -> tuple[bool, list[str]]:
    found = [p for p in sf.exclude_with if p.lower() in text]
    if found:
        return False, [f"{field_name}.exclude_with triggered: {found}"]
    return True, [f"{field_name}.exclude_with passed: {sf.exclude_with}"]


_STRING_CHECKS: tuple[_StringCheck, ...] = (
    _StringCheck("contains", lambda sf: bool(sf.contains), _check_contains),
    _StringCheck(
        "contains_any", lambda sf: bool(sf.contains_any), _check_contains_any
    ),
    _StringCheck("matches", lambda sf: bool(sf.matches), _check_matches),
    _StringCheck(
        "exclude_with", lambda sf: bool(sf.exclude_with), _check_exclude_with
    ),
)


def check_string_filter(
    text: str, sf: StringFilter, field_name: str = "field"
) -> tuple[bool, list[str]]:
    """Check if text passes all sub-filters in a StringFilter."""
    reasons = []
    t = text.lower()

    for check in _STRING_CHECKS:
        if not check.is_active(sf):
            continue
        # Regex matching is case-insensitive via compiled flags; substring
        # checks use the lowercased text.
        check_text = text if check.name == "matches" else t
        ok, r = check.run(check_text, sf, field_name)
        reasons.extend(r)
        if not ok:
            return False, reasons

    return True, reasons


def entry_matches_source(
    entry: ReaderFeedEntry, match: SourceMatch | None
) -> tuple[bool, list[str]]:
    """Check if an entry belongs to a source defined by SourceMatch."""
    if match is None:
        return True, []

    reasons = []

    for fld, entry_val, filter_attr in (
        ("site_names", entry.site_name, "site_names_filter"),
        ("authors", entry.author, "authors_filter"),
        ("categories", entry.category, "categories_filter"),
    ):
        sf = getattr(match, filter_attr)
        if sf is not None:
            ok, r = check_string_filter(entry_val or "", sf, field_name=fld)
            reasons.extend(r)
            if not ok:
                return False, reasons
            reasons.append(f"source.{fld} matched: {entry_val}")

    if match.site_names:
        if entry.site_name.lower() not in {s.lower() for s in match.site_names}:
            reasons.append(
                f"source.site_names '{entry.site_name}' not in {match.site_names}"
            )
            return False, reasons
        reasons.append(f"source.site_names matched: {entry.site_name}")

    if match.authors:
        if entry.author.lower() not in {a.lower() for a in match.authors}:
            reasons.append(f"source.authors '{entry.author}' not in {match.authors}")
            return False, reasons
        reasons.append(f"source.authors matched: {entry.author}")

    if match.domains:
        entry_domain = _extract_domain(entry.source_url)
        if not any(_domain_matches(entry_domain, d) for d in match.domains):
            reasons.append(
                f"source.domains '{entry_domain}' "
                f"not in {match.domains}"
            )
            return False, reasons
        matched = [d for d in match.domains if _domain_matches(entry_domain, d)]
        reasons.append(f"source.domains matched: {matched}")

    if match.categories:
        if entry.category.lower() not in {c.lower() for c in match.categories}:
            reasons.append(
                f"source.categories '{entry.category}' not in {match.categories}"
            )
            return False, reasons
        reasons.append(f"source.categories matched: {entry.category}")

    return True, reasons


# Mapping: rule field name -> entry attribute name
_STRING_FILTER_FIELDS: dict[str, str] = {
    "title": "title",
    "summary": "summary",
    "content": "html_content",
    "url": "source_url",
}


def _extract_domain(url: str) -> str:
    """Extract domain from a URL, stripping www. prefix."""
    hostname = urlparse(url).hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _domain_matches(entry_domain: str, target_domain: str) -> bool:
    """Check if entry_domain matches target_domain, including subdomains."""
    target = target_domain.lower()
    entry = entry_domain.lower()
    return entry == target or entry.endswith("." + target)


def _check_string_filter_field(
    entry: ReaderFeedEntry, rule: FilterRule, field_name: str, entry_attr: str
) -> tuple[bool, list[str]]:
    """Check a single string filter field (title, summary, content, url)."""
    text = getattr(entry, entry_attr) or ""
    sf = getattr(rule, field_name)
    ok, r = check_string_filter(text, sf, field_name=field_name)
    label = "passed" if ok else "failed"
    return ok, [f"  {field_name} filter {label}: {'; '.join(r)}"]


def _check_tags(
    entry: ReaderFeedEntry, rule: FilterRule
) -> tuple[list[str], bool]:
    reasons: list[str] = []
    all_passed = True
    entry_tags = {str(t).lower() for t in entry.tags if t is not None}

    if rule.tags_include:
        missing = [t for t in rule.tags_include if t.lower() not in entry_tags]
        if missing:
            reasons.append(f"  tags_include missing: {missing}")
            all_passed = False
        else:
            reasons.append(f"  tags_include matched: {rule.tags_include}")

    if rule.tags_exclude:
        found = [t for t in rule.tags_exclude if t.lower() in entry_tags]
        if found:
            reasons.append(f"  tags_exclude triggered: {found}")
            all_passed = False
        else:
            reasons.append(f"  tags_exclude passed: {rule.tags_exclude}")

    return reasons, all_passed


def _check_age(entry: ReaderFeedEntry, rule: FilterRule) -> tuple[bool, list[str]]:
    if entry.created_at is None:
        return False, ["  age skipped: no created_at date — rule does not match"]

    age_days = (datetime.now(timezone.utc) - entry.created_at).days
    threshold = rule.age.older_than_days
    if age_days <= threshold:
        return False, [f"  age {age_days}d <= {threshold}d (too recent)"]
    return True, [f"  age {age_days}d > {threshold}d (old enough)"]


def _check_read(entry: ReaderFeedEntry, rule: FilterRule) -> tuple[bool, list[str]]:
    if rule.read is None:
        return True, []
    entry_is_read = entry.first_opened_at is not None
    if rule.read == entry_is_read:
        label = "read" if rule.read else "unread"
        return True, [f"  read filter passed: entry is {label}"]
    expected = "read" if rule.read else "unread"
    actual = "read" if entry_is_read else "unread"
    return False, [f"  read filter failed: expected {expected}, entry is {actual}"]


def _apply_check(
    all_passed: bool,
    reasons: list[str],
    ok: bool,
    r: list[str],
) -> bool:
    """Append check results and update all_passed flag."""
    reasons.extend(r)
    return all_passed and ok


def entry_matches_rule(
    entry: ReaderFeedEntry, rule: FilterRule
) -> tuple[bool, list[str]]:
    """Check if an entry matches all filter fields in a rule."""
    reasons: list[str] = []
    all_passed = True

    for field_name, entry_attr in _STRING_FILTER_FIELDS.items():
        sf = getattr(rule, field_name)
        if sf is None:
            continue
        ok, r = _check_string_filter_field(entry, rule, field_name, entry_attr)
        all_passed = _apply_check(all_passed, reasons, ok, r)

    if rule.tags_include or rule.tags_exclude:
        tag_reasons, tags_passed = _check_tags(entry, rule)
        all_passed = _apply_check(all_passed, reasons, tags_passed, tag_reasons)

    if rule.age is not None and rule.age.older_than_days is not None:
        ok, r = _check_age(entry, rule)
        all_passed = _apply_check(all_passed, reasons, ok, r)

    if rule.read is not None:
        ok, r = _check_read(entry, rule)
        all_passed = _apply_check(all_passed, reasons, ok, r)

    if all_passed:
        reasons.append(f"  => action: {rule.action}")

    return all_passed, reasons


def resolve_action(matched: list[MatchResult]) -> MatchResult:
    """Pick the winning action from multiple matched rules.

    Higher ACTION_PRECEDENCE wins. On ties, global scope wins over feed scope.
    """
    if not matched:
        raise ValueError("resolve_action called with empty match list")

    best = max(
        matched,
        key=lambda m: (
            ACTION_PRECEDENCE.get(m.rule.action, 0),
            0 if m.scope == "global" else 1,
        ),
    )
    merged = MatchResult(
        scope=best.scope,
        rule=best.rule,
        reasons=list(best.reasons),
    )
    for m in matched:
        if m is not best:
            merged.reasons.append(f"  (overridden: {m.rule.action} from {m.scope})")
    return merged
