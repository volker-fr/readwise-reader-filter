"""Data models for Readwise Reader documents."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ReaderFeedEntry:
    id: str
    title: str
    author: str
    source_url: str
    site_name: str
    summary: str
    location: str
    category: str
    tags: list[str]
    notes: str
    html_content: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    first_opened_at: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict) -> "ReaderFeedEntry":
        raw_id = data.get("id")
        return cls(
            id="" if raw_id is None else str(raw_id),
            title=data.get("title") or "",
            author=data.get("author") or "",
            source_url=data.get("source_url") or "",
            site_name=data.get("site_name") or "",
            summary=data.get("summary") or "",
            html_content=data.get("html_content") or "",
            location=data.get("location") or "",
            category=data.get("category") or "",
            tags=data.get("tags") or [],
            notes=data.get("notes") or "",
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
            first_opened_at=_parse_dt(data.get("first_opened_at")),
            raw=dict(data),
        )


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str) and value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, OSError, OverflowError):
        return None
