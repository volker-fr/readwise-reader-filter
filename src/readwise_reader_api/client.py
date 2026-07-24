"""Readwise Reader API client with restricted update capabilities.

Rate Limits (per access token):
    - LIST (GET /api/v3/list/): 20/minute
    - UPDATE (PATCH /api/v3/update/): 50/minute
    - DELETE (DELETE /api/v3/delete/): 20/minute
    - TAGS (GET /api/v3/tags/): 20/minute

When rate limited (429), check Retry-After header for wait time.
"""

import json as _json
import logging
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

import requests

from .cache import Cache

logger = logging.getLogger(__name__)

# HTTP status codes used in retry / error handling
HTTP_TOO_MANY_REQUESTS = 429
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR_THRESHOLD = 500

# Default operational constants
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_CACHE_TTL_SECONDS = 3600
STATE_CACHE_TTL_SECONDS = 7776000  # 90 days
LIST_PAGE_SIZE = 100
MAX_LIST_PAGES = 500

# Exponential backoff base, in seconds
BACKOFF_BASE_SECONDS = 2

# Bulk update batch size
BULK_LIMIT = 50


class ReadwiseReaderError(Exception):
    """Base exception for Readwise Reader errors."""


class RateLimitError(ReadwiseReaderError):
    """Raised when rate limited (429)."""

    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        msg = "Rate limited."
        if retry_after is not None:
            msg += f" Retry after {retry_after} seconds."
        super().__init__(msg)


class ReadwiseReaderAPIClient:
    """Client for Readwise Reader API v3.

    Only exposes safe, append-style operations:
    - List documents with filters
    - Update summary (append-style)
    - Update tags (append-style)
    """

    BASE_URL = "https://readwise.io/api/v3/"
    ALLOWED_UPDATE_FIELDS = frozenset(
        {"summary", "tags", "location", "notes", "title"}
    )

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> int | None:
        """Extract wait time in seconds from a 429 response.

        Checks Retry-After header (seconds or HTTP-date), then response
        body keys, then message text. Returns None if no wait time found.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            if retry_after.isdigit():
                return int(retry_after)
            try:
                retry_date = parsedate_to_datetime(retry_after)
                now = datetime.now(timezone.utc)
                return max(0, int((retry_date - now).total_seconds()))
            except (ValueError, TypeError):
                pass

        try:
            body = response.json()
            for key in (
                "retry_after",
                "retry-after",
                "retryAfter",
                "wait",
                "seconds",
            ):
                if key in body and str(body[key]).isdigit():
                    return int(body[key])
            msg = body.get("message", body.get("detail", ""))
            match = re.search(r"(\d+)\s*s(?:econds?)?", msg, re.IGNORECASE)
            if match:
                return int(match.group(1))
        except (ValueError, AttributeError):
            pass

        return None

    def __init__(
        self,
        api_token: str | None = None,
        use_cache: bool = True,
        cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
        cache_dir: str | Path | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.api_token = api_token or os.getenv("READWISE_API_TOKEN")
        if not self.api_token:
            raise ReadwiseReaderError(
                "API token required. Set READWISE_API_TOKEN env var "
                "or pass api_token parameter."
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self._headers = {
            "Authorization": f"Token {self.api_token}",
            "Content-Type": "application/json",
        }
        if cache_dir is not None:
            cache_base = Path(cache_dir)
        else:
            cache_base = Path.home() / ".cache" / "readwise_reader_api"
        self._cache_api = (
            Cache(cache_dir=str(cache_base), ttl=cache_ttl) if use_cache else None
        )
        self._cache_state = Cache(
            cache_dir=str(cache_base / "state"),
            ttl=STATE_CACHE_TTL_SECONDS,
        )
        self._max_retries = max_retries

    @staticmethod
    def _backoff_wait(attempt: int) -> int:
        """Exponential backoff wait time in seconds."""
        return BACKOFF_BASE_SECONDS**attempt

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an API request with rate limit handling and retry."""
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)

        for attempt in range(self._max_retries + 1):
            try:
                response = requests.request(method, url, **kwargs)
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < self._max_retries:
                    wait = self._backoff_wait(attempt)
                    logger.warning(
                        "%s on %s. Retrying in %ds (attempt %d/%d)",
                        type(e).__name__,
                        url,
                        wait,
                        attempt + 1,
                        self._max_retries,
                    )
                    time.sleep(wait)
                    continue
                logger.warning(
                    "%s on %s after %d retries",
                    type(e).__name__,
                    url,
                    self._max_retries,
                )
                raise

            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                wait = self._parse_retry_after(response)
                if wait is None:
                    wait = self._backoff_wait(attempt)
                    wait_source = "exponential fallback"
                else:
                    wait_source = "Retry-After"
                logger.warning(
                    "Rate limited on %s. wait=%ds (from %s)",
                    url,
                    wait,
                    wait_source,
                )
                if attempt < self._max_retries:
                    logger.warning(
                        "  Retrying in %ds (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        self._max_retries,
                    )
                    time.sleep(wait)
                    continue
                logger.warning("  Max retries (%d) exceeded.", self._max_retries)
                raise RateLimitError(wait)

            if (
                response.status_code >= HTTP_SERVER_ERROR_THRESHOLD
                and attempt < self._max_retries
            ):
                wait = self._backoff_wait(attempt)
                logger.warning(
                    "Server error %d on %s. Retrying in %ds (attempt %d/%d)",
                    response.status_code,
                    url,
                    wait,
                    attempt + 1,
                    self._max_retries,
                )
                time.sleep(wait)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                raise ReadwiseReaderError(str(e)) from e
            return response

        raise ReadwiseReaderError("Request failed: max retries exhausted")

    def _paginate_list(
        self, params: dict, max_pages: int = MAX_LIST_PAGES
    ) -> list[dict]:
        """Internal: paginate through the list endpoint with given params."""
        documents = []
        next_cursor = None
        pages = 0
        params = dict(params)

        while pages < max_pages:
            if next_cursor:
                params["pageCursor"] = next_cursor

            response = self._request(
                "GET",
                f"{self.BASE_URL}list/",
                headers=self._headers,
                params=params,
            )
            data = response.json()

            documents.extend(data.get("results") or [])
            pages += 1

            next_cursor = data.get("nextPageCursor")
            if not next_cursor:
                break

        return documents

    def list_documents(
        self,
        location: (
            Literal["new", "later", "shortlist", "archive", "feed"] | None
        ) = None,
        category: (
            Literal[
                "article",
                "email",
                "rss",
                "highlight",
                "note",
                "pdf",
                "epub",
                "tweet",
                "video",
            ]
            | None
        ) = None,
        tag: str | None = None,
        updated_after: str | None = None,
        limit: int = LIST_PAGE_SIZE,
        with_html_content: bool = False,
        force_refresh: bool = False,
    ) -> list[dict]:
        """List documents with optional filters.

        Note: Client-side filtering is needed for fields not supported
        by the API (source_url, site_name, author).
        """
        cache_key = "list_" + _json.dumps(
            {
                "location": location,
                "category": category,
                "tag": tag,
                "updated_after": updated_after,
                "limit": limit,
                "with_html": with_html_content,
            },
            sort_keys=True,
        )
        if not force_refresh and self._cache_api:
            cached = self._cache_api.get(cache_key)
            if cached is not None:
                return cached

        params: dict[str, str] = {"limit": str(limit)}
        if location:
            params["location"] = location
        if category:
            params["category"] = category
        if tag:
            params["tag"] = tag
        if updated_after:
            params["updatedAfter"] = updated_after
        if with_html_content:
            params["withHtmlContent"] = "true"

        documents = self._paginate_list(params)

        if self._cache_api:
            self._cache_api.set(cache_key, documents)

        return documents

    def get_all_documents(self, force_refresh: bool = False) -> list[dict]:
        """Fetch and cache ALL documents (paginated).

        Downloads all documents across all locations, caches them,
        then you can search/filter from the cache.

        Args:
            force_refresh: If True, bypass cache and re-download.

        Returns:
            All documents from Readwise Reader.
        """
        cache_key = "all_documents"
        if not force_refresh and self._cache_api:
            cached = self._cache_api.get(cache_key)
            if cached is not None:
                return cached

        params: dict[str, str] = {"limit": str(LIST_PAGE_SIZE)}
        documents = self._paginate_list(params)

        if self._cache_api:
            self._cache_api.set(cache_key, documents)

        return documents

    def _find_document_by_id(
        self, document_id: str, location: str | None = None
    ) -> dict | None:
        """Find a single document by ID, optionally scoped to a location.

        Searches the given location first, then falls back to an unscoped
        search if the document is not found (it may have been moved).
        Uses cache by default; passes force_refresh to skip cache.
        """
        docs = self.list_documents(location=location)
        found = next((d for d in docs if d.get("id") == document_id), None)
        if not found and location is not None:
            docs = self.list_documents()
            found = next((d for d in docs if d.get("id") == document_id), None)
        return found

    def update_summary(
        self,
        document_id: str,
        summary: str,
        prefix: bool = True,
        location: str | None = None,
    ) -> dict:
        """Update document summary.

        Args:
            document_id: Document ID
            summary: New summary content
            prefix: If True, prepend new summary to existing (separated by '-----')
            location: Optional location filter to narrow the search scope
        """
        if prefix:
            existing = self._find_document_by_id(document_id, location)
            if existing and existing.get("summary"):
                summary = f"{summary}\n-----\n{existing['summary']}"

        return self._update(document_id, summary=summary)

    def upsert_summary(
        self, document_id: str, new_content: str, location: str | None = None
    ) -> bool:
        """Update summary only if new content is not already present.

        Args:
            document_id: Document ID
            new_content: Content to add
            location: Optional location filter to narrow the search scope

        Returns:
            True if updated, False if already present.
        """
        existing = self._find_document_by_id(document_id, location)
        if existing and existing.get("summary"):
            if new_content in existing["summary"]:
                return False
            new_content = f"{new_content}\n-----\n{existing['summary']}"

        self._update(document_id, summary=new_content)
        return True

    def update_tags(self, document_id: str, tags: list[str]) -> dict:
        """Update document tags (replaces all existing tags).

        Args:
            document_id: Document ID
            tags: List of tag strings
        """
        return self._update(document_id, tags=tags)

    def update_location(self, document_id: str, location: str) -> dict:
        """Update document location.

        Args:
            document_id: Document ID
            location: Location to move to (new, later, shortlist, archive, feed)
        """
        return self._update(document_id, location=location)

    def update_title(self, document_id: str, title: str) -> dict:
        """Update document title.

        Args:
            document_id: Document ID
            title: New title for the document
        """
        return self._update(document_id, title=title)

    def is_marked_for_delete(self, document_id: str) -> bool:
        """Check if a document was already marked for delete (state cache hit)."""
        cache_key = f"delete_{document_id}"
        return bool(self._cache_state and self._cache_state.get(cache_key))

    def mark_for_delete(
        self, document_id: str, reason: str = "", tags: list[str] | None = None
    ) -> bool:
        """Mark document for delete (add tag + move to later).

        Args:
            document_id: Document ID
            reason: Optional reason to add to notes field
            tags: Current document tags (avoids an extra API call).
                   If None, the document's tags are not modified.

        Uses local cache to avoid re-tagging same document.

        Raises:
            ReadwiseReaderError: On API errors during the operation.
        """
        cache_key = f"delete_{document_id}"
        if self._cache_state and self._cache_state.get(cache_key):
            return True

        partial_failure = False

        if tags is not None:
            current_tags = [t for t in tags if t is not None]
            if not any(t.lower() == "delete" for t in current_tags):
                current_tags.append("delete")
            try:
                self.update_tags(document_id, current_tags)
            except ReadwiseReaderError:
                partial_failure = True
                logger.warning(
                    "Failed to update tags for %s, continuing with location change",
                    document_id,
                )

        try:
            self.update_location(document_id, "later")
        except ReadwiseReaderError:
            if self._cache_state:
                self._cache_state.invalidate_key(cache_key)
            raise

        try:
            if reason:
                self._update(document_id, notes=f"DELETE: {reason}")
        except ReadwiseReaderError:
            partial_failure = True
            logger.warning(
                "Failed to update notes for %s, document already marked",
                document_id,
            )

        if self._cache_state and not partial_failure:
            self._cache_state.set(cache_key, True)
        return True

    def delete_document(self, document_id: str) -> bool:
        """Delete a document from Readwise Reader.

        Args:
            document_id: Document ID to delete

        Returns:
            True if deleted successfully (or already deleted).

        Raises:
            ReadwiseReaderError: On API errors (e.g. rate limiting).
        """
        try:
            self._request(
                "DELETE",
                f"{self.BASE_URL}delete/{document_id}/",
                headers=self._headers,
            )
            self._invalidate_list_cache()
            if self._cache_state:
                self._cache_state.invalidate_key(f"delete_{document_id}")
            return True
        except ReadwiseReaderError as e:
            if self._is_not_found_error(e):
                return True
            raise

    def bulk_update(self, updates: list[dict]) -> int:
        """Send batch updates via bulk_update/ endpoint.

        Args:
            updates: List of dicts, each with "id" and one or more fields
                     to update (summary, tags, location, notes, title, seen).

        Returns:
            Number of documents successfully updated.

        Raises:
            ReadwiseReaderError: On 400 (bad payload) or other non-recoverable errors.
        """
        if not updates:
            return 0

        updated = 0

        for i in range(0, len(updates), BULK_LIMIT):
            batch = updates[i : i + BULK_LIMIT]
            response = self._request(
                "PATCH",
                f"{self.BASE_URL}bulk_update/",
                headers=self._headers,
                json={"updates": batch},
            )
            data = response.json()
            for result in data.get("results", []):
                if result.get("success"):
                    updated += 1
                else:
                    doc_id = result.get("id", "unknown")
                    error = result.get("error", "unknown error")
                    logger.warning("    Bulk update failed for %s: %s", doc_id, error)
            self._invalidate_list_cache()

        return updated

    def mark_seen(self, document_ids: list[str], seen: bool = True) -> int:
        """Mark documents as seen/unseen via bulk update.

        Args:
            document_ids: List of document IDs to update
            seen: True to mark as seen, False to mark as unseen

        Returns:
            Number of documents successfully updated.

        Raises:
            ReadwiseReaderError: On API errors.
        """
        updates = [{"id": doc_id, "seen": seen} for doc_id in document_ids]
        return self.bulk_update(updates)

    @staticmethod
    def _is_not_found_error(error: ReadwiseReaderError) -> bool:
        """Check if an error represents an HTTP 404 response."""
        cause = getattr(error, "__cause__", None)
        if isinstance(cause, requests.HTTPError):
            response = getattr(cause, "response", None)
            if response is not None and response.status_code == HTTP_NOT_FOUND:
                return True
        return "404" in str(error)

    def clear_list_cache(self) -> None:
        if not self._cache_api:
            return
        self._cache_api.invalidate_prefix("list_")

    def invalidate_cache(self) -> None:
        self._invalidate_list_cache()

    def _invalidate_list_cache(self) -> None:
        """Invalidate list cache after a mutation."""
        if self._cache_api:
            self._cache_api.invalidate_prefix("list_")
            self._cache_api.invalidate_key("all_documents")

    def _update(self, document_id: str, **fields) -> dict:
        """Internal update method - only allows safe fields."""
        disallowed = set(fields.keys()) - self.ALLOWED_UPDATE_FIELDS
        if disallowed:
            raise ReadwiseReaderError(
                f"Field(s) not allowed for update: {disallowed}. "
                f"Allowed: {self.ALLOWED_UPDATE_FIELDS}"
            )

        response = self._request(
            "PATCH",
            f"{self.BASE_URL}update/{document_id}/",
            headers=self._headers,
            json=fields,
        )
        self._invalidate_list_cache()
        return response.json()
