"""YAML config loading, schema validation, and type coercion."""

import logging
import re
from pathlib import Path

import yaml

from .types import (
    VALID_ACTIONS,
    AgeFilter,
    FeedDefinition,
    FilterConfig,
    FilterConfigError,
    FilterRule,
    SourceMatch,
    StringFilter,
    _coerce_str_list,
)

logger = logging.getLogger(__name__)

KNOWN_RULE_KEYS = frozenset(
    {
        "name",
        "title",
        "summary",
        "content",
        "url",
        "tags_include",
        "tags_exclude",
        "age",
        "read",
        "action",
    }
)

KNOWN_FEED_KEYS = frozenset({"name", "match", "rules"})

KNOWN_STRING_FILTER_KEYS = frozenset(
    {
        "contains",
        "contains_any",
        "matches",
        "exclude_with",
    }
)

KNOWN_SOURCE_MATCH_KEYS = frozenset(
    {
        "site_names",
        "authors",
        "domains",
        "categories",
    }
)

KNOWN_AGE_FILTER_KEYS = frozenset({"older_than_days"})

RULE_FILTER_FIELDS = frozenset(
    {
        "title",
        "summary",
        "content",
        "url",
        "tags_include",
        "tags_exclude",
        "age",
        "read",
    }
)

KNOWN_TOP_LEVEL_KEYS = frozenset({"global", "feeds", "mark_all_as_unseen"})


def _check_keys(d: dict, allowed: frozenset, label: str, prefix: str) -> list[str]:
    errors = []
    for key in d:
        if key not in allowed:
            errors.append(
                f"{prefix}unknown key '{key}' in {label} "
                f"(valid: {', '.join(sorted(allowed))})"
            )
    return errors


def _validate_string_filter(d: dict, label: str, prefix: str) -> list[str]:
    errors = _check_keys(d, KNOWN_STRING_FILTER_KEYS, label, prefix)
    for pat in d.get("matches") or []:
        if isinstance(pat, str):
            try:
                re.compile(pat)
            except re.error as e:
                errors.append(f"{prefix}invalid regex in matches: {pat!r}: {e}")
    return errors


def _check_age_filter(d: dict, label: str, prefix: str) -> list[str]:
    errors = _check_keys(d, KNOWN_AGE_FILTER_KEYS, label, prefix)
    days = d.get("older_than_days")
    if days is not None and not isinstance(days, int) or isinstance(days, bool):
        errors.append(
            f"{prefix}'older_than_days' must be a positive integer, "
            f"got {type(days).__name__}"
        )
    elif isinstance(days, int) and days < 1:
        errors.append(
            f"{prefix}'older_than_days' must be >= 1, got {days}"
        )
    return errors


def _check_source_match(d: dict, label: str, prefix: str) -> list[str]:
    errors = _check_keys(d, KNOWN_SOURCE_MATCH_KEYS, label, prefix)
    for fld in ("site_names", "authors", "categories"):
        val = d.get(fld)
        if isinstance(val, dict):
            errors.extend(_validate_string_filter(val, f"{fld} filter", prefix))
        elif val is not None:
            if not isinstance(val, list):
                errors.append(
                    f"{prefix}'{fld}' must be a list, dict, or omitted, "
                    f"got {type(val).__name__}"
                )
            elif not all(isinstance(v, str) for v in val if v is not None):
                errors.append(f"{prefix}'{fld}' must contain only strings")

    domains = d.get("domains")
    if domains is not None:
        if not isinstance(domains, list):
            errors.append(
                f"{prefix}'domains' must be a list, got {type(domains).__name__}"
            )
        elif not all(isinstance(v, str) for v in domains if v is not None):
            errors.append(f"{prefix}'domains' must contain only strings")
    return errors


def _check_rule(d: dict, label: str, prefix: str) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []

    if not isinstance(d, dict):
        errors.append(f"{prefix}{label} must be a dict, got {type(d).__name__}")
        return errors, warnings

    errors.extend(_check_keys(d, KNOWN_RULE_KEYS, label, prefix))

    for fld in ("title", "summary", "content", "url"):
        val = d.get(fld)
        if isinstance(val, dict):
            errors.extend(_validate_string_filter(val, f"{fld} filter", prefix))
        elif val is not None and not isinstance(val, str):
            errors.append(
                f"{prefix}'{fld}' must be a string or dict, "
                f"got {type(val).__name__}"
            )

    if isinstance(d.get("age"), dict):
        errors.extend(_check_age_filter(d["age"], "age filter", prefix))

    read_val = d.get("read")
    if read_val is not None and not isinstance(read_val, bool):
        errors.append(
            f"{prefix}'read' must be true or false, "
            f"got {type(read_val).__name__}"
        )

    action = d.get("action", "archive")
    if action not in VALID_ACTIONS:
        errors.append(
            f"{prefix}invalid action '{action}' in rule '{d.get('name', '')}' "
            f"(valid: {', '.join(sorted(VALID_ACTIONS))})"
        )

    if not any(d.get(f) for f in RULE_FILTER_FIELDS):
        name = d.get("name", "unnamed")
        warnings.append(
            f"{prefix}rule '{name}' has no filter fields — "
            f"it will match every entry"
        )

    return errors, warnings


def _validate_global_rules(data: dict) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []
    raw_global = data.get("global") or []
    if not isinstance(raw_global, list):
        errors.append(
            f"'global' must be a list of rules, got {type(raw_global).__name__}"
        )
        return errors, warnings

    for i, rule in enumerate(raw_global):
        e, w = _check_rule(rule, "global rule", f"global[{i}]: ")
        errors.extend(e)
        warnings.extend(w)
    return errors, warnings


def _validate_feeds(data: dict) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []
    raw_feeds = data.get("feeds") or []
    if not isinstance(raw_feeds, list):
        errors.append(f"'feeds' must be a list, got {type(raw_feeds).__name__}")
        return errors, warnings

    for i, fd in enumerate(raw_feeds):
        if not isinstance(fd, dict):
            errors.append(
                f"feeds[{i}]: feed definition must be a dict, "
                f"got {type(fd).__name__}"
            )
            continue
        feed_label = fd.get("name", f"feeds[{i}]")
        prefix = f"feeds[{feed_label}]: "
        errors.extend(_check_keys(fd, KNOWN_FEED_KEYS, "feed definition", prefix))

        match_data = fd.get("match")
        if isinstance(match_data, dict):
            errors.extend(
                _check_source_match(match_data, "source match", prefix + "match: ")
            )
        elif match_data is not None:
            errors.append(
                f"{prefix}'match' must be a dict or omitted, "
                f"got {type(match_data).__name__}"
            )

        raw_rules = fd.get("rules") or []
        if not isinstance(raw_rules, list):
            errors.append(
                f"{prefix}'rules' must be a list, got {type(raw_rules).__name__}"
            )
            continue
        if not raw_rules:
            warnings.append(
                f"{prefix}feed has no rules — "
                f"entries will match but nothing will happen"
            )
            continue
        for j, rule in enumerate(raw_rules):
            e, w = _check_rule(rule, "feed rule", f"{prefix}rules[{j}]: ")
            errors.extend(e)
            warnings.extend(w)

    return errors, warnings


def _validate_top_level(data: dict) -> list[str]:
    errors = []
    for key in data:
        if key not in KNOWN_TOP_LEVEL_KEYS:
            errors.append(
                f"unknown top-level key '{key}' "
                f"(valid: {', '.join(sorted(KNOWN_TOP_LEVEL_KEYS))})"
            )
    return errors


def _coerce_rule_dict(raw: dict) -> dict:
    """Convert a raw YAML rule dict into types FilterRule expects.

    Handles dict→dataclass for title/summary/url/age, and str/list
    normalization for tags_include/tags_exclude.
    """
    out = dict(raw)
    for fld in ("title", "summary", "content", "url"):
        val = out.get(fld)
        if isinstance(val, str):
            out[fld] = StringFilter(contains=[val]) if val else None
        elif isinstance(val, dict):
            out[fld] = StringFilter(**val)
        elif val is not None and not isinstance(val, StringFilter):
            raise TypeError(
                f"'{fld}' must be a string, dict, or StringFilter, "
                f"got {type(val).__name__}"
            )
    for fld in ("tags_include", "tags_exclude"):
        out[fld] = _coerce_str_list(out.get(fld))
    if isinstance(out.get("age"), dict):
        out["age"] = AgeFilter(**out["age"])
    return out


def _validate_mark_all_as_unseen(data: dict) -> list[str]:
    """Validate mark_all_as_unseen config and return warnings."""
    warnings = []
    if not data.get("mark_all_as_unseen"):
        return warnings

    seen_unseen_rules = []
    for rule in data.get("global") or []:
        if isinstance(rule, dict) and rule.get("action") in ("seen", "unseen"):
            seen_unseen_rules.append(rule.get("name", "unnamed"))
    for feed in data.get("feeds") or []:
        if isinstance(feed, dict):
            for rule in feed.get("rules") or []:
                if isinstance(rule, dict) and rule.get("action") in (
                    "seen",
                    "unseen",
                ):
                    seen_unseen_rules.append(rule.get("name", "unnamed"))
    if seen_unseen_rules:
        warnings.append(
            f"mark_all_as_unseen is enabled but rules use seen/unseen actions: "
            f"{', '.join(seen_unseen_rules)} — mark_all_as_unseen will override"
        )
    return warnings


def _validate_config(data: dict) -> list[str]:
    """Validate a raw config dict before parsing.

    Returns a list of human-readable error strings. Warnings are logged at
    WARNING level but do not prevent the config from being used.
    """
    errors = []
    warnings = []

    errors.extend(_validate_top_level(data))

    e, w = _validate_global_rules(data)
    errors.extend(e)
    warnings.extend(w)

    e, w = _validate_feeds(data)
    errors.extend(e)
    warnings.extend(w)

    warnings.extend(_validate_mark_all_as_unseen(data))

    if warnings:
        for w in warnings:
            logger.warning("Config warning: %s", w)

    return errors


def validate_config(path: str | Path) -> list[str]:
    """Validate a config file and return any errors found.

    Returns a list of error strings (empty if valid).
    Raises FilterConfigError if the file cannot be read or parsed.
    """
    path = Path(path)
    if not path.exists():
        raise FilterConfigError(f"Config file not found: {path}")
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        raise FilterConfigError(f"Error reading config file {path}: {e}") from e
    errors = _validate_config(data or {})
    if errors:
        for e in errors:
            logger.error("  %s", e)
    else:
        logger.info("Config file %s is valid.", path)
    return errors


def _from_dict(data: dict) -> FilterConfig:
    """Build a FilterConfig from a parsed YAML dict."""
    raw_global = data.get("global") or []
    if not isinstance(raw_global, list):
        raise TypeError(
            f"'global' must be a list of rules, got {type(raw_global).__name__}"
        )
    global_rules = [FilterRule(**_coerce_rule_dict(r)) for r in raw_global]
    raw_feeds = data.get("feeds") or []
    if not isinstance(raw_feeds, list):
        raise TypeError(f"'feeds' must be a list, got {type(raw_feeds).__name__}")
    feeds = []
    for fd in raw_feeds:
        match_data = fd.get("match")
        raw_rules = fd.get("rules") or []
        if not isinstance(raw_rules, list):
            raise TypeError(
                f"rules for feed '{fd.get('name', '')}' must be a list, "
                f"got {type(raw_rules).__name__}"
            )
        rules = [FilterRule(**_coerce_rule_dict(r)) for r in raw_rules]
        match_kwargs = dict(match_data) if match_data else {}
        for fld in ("site_names", "authors", "categories"):
            if isinstance(match_kwargs.get(fld), dict):
                match_kwargs[fld] = StringFilter(**match_kwargs[fld])
        feeds.append(
            FeedDefinition(
                name=fd.get("name", ""),
                match=SourceMatch(**match_kwargs) if match_data else None,
                rules=rules,
            )
        )
    return FilterConfig(
        global_rules=global_rules,
        feeds=feeds,
        mark_all_as_unseen=bool(data.get("mark_all_as_unseen")),
    )


def load_config(path: str | Path) -> FilterConfig:
    """Load and validate a config file, returning a FilterConfig.

    Raises FilterConfigError if the file is missing, unparseable, or
    contains validation errors.
    """
    path = Path(path)
    if not path.exists():
        raise FilterConfigError(f"Config file not found: {path}")
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        raise FilterConfigError(f"Error reading config file {path}: {e}") from e
    errors = _validate_config(data or {})
    if errors:
        msg = f"Config file {path} has {len(errors)} error(s):\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise FilterConfigError(msg)
    try:
        return _from_dict(data or {})
    except (TypeError, ValueError) as e:
        raise FilterConfigError(
            f"Invalid rule definition in config file {path}: {e}"
        ) from e
