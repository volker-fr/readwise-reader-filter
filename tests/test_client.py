"""Unit tests for readwise_reader_api/client.py."""

from unittest.mock import MagicMock, patch

import pytest

from readwise_reader_api.cache import Cache
from readwise_reader_api.client import (
    RateLimitError,
    ReadwiseReaderAPIClient,
    ReadwiseReaderError,
)


@pytest.fixture
def api(tmp_path):
    """Create a client with mocked auth and tmp cache dirs."""
    with patch.dict("os.environ", {"READWISE_API_TOKEN": "test-token"}):
        client = ReadwiseReaderAPIClient(
            use_cache=True,
            cache_ttl=3600,
        )
        client._cache_api = Cache(cache_dir=str(tmp_path / "api"), ttl=3600)
        client._cache_state = Cache(cache_dir=str(tmp_path / "state"), ttl=7776000)
    return client


@pytest.fixture
def api_no_cache():
    """Create a client with no cache."""
    with patch.dict("os.environ", {"READWISE_API_TOKEN": "test-token"}):
        return ReadwiseReaderAPIClient(use_cache=False)


# ── Initialization ────────────────────────────────────────────────


class TestClientInit:
    def test_requires_api_token(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ReadwiseReaderError, match="API token required"),
        ):
            ReadwiseReaderAPIClient()

    def test_max_retries_validation(self):
        with (
            patch.dict("os.environ", {"READWISE_API_TOKEN": "tok"}),
            pytest.raises(ValueError, match="max_retries must be >= 0"),
        ):
            ReadwiseReaderAPIClient(max_retries=-1)


# ── _parse_retry_after ────────────────────────────────────────────


class TestParseRetryAfter:
    def test_integer_header(self):
        response = MagicMock()
        response.headers = {"Retry-After": "30"}
        assert ReadwiseReaderAPIClient._parse_retry_after(response) == 30

    def test_http_date_header(self):
        import time
        from email.utils import formatdate

        future = int(time.time()) + 60
        response = MagicMock()
        response.headers = {"Retry-After": formatdate(timeval=future, usegmt=True)}
        result = ReadwiseReaderAPIClient._parse_retry_after(response)
        assert result is not None
        assert 55 <= result <= 65

    def test_body_key_retry_after(self):
        response = MagicMock()
        response.headers = {}
        response.json.return_value = {"retry_after": 15}
        assert ReadwiseReaderAPIClient._parse_retry_after(response) == 15

    def test_body_message_seconds(self):
        response = MagicMock()
        response.headers = {}
        response.json.return_value = {"message": "Rate limited. Try again in 10s."}
        assert ReadwiseReaderAPIClient._parse_retry_after(response) == 10

    def test_no_retry_info_returns_none(self):
        response = MagicMock()
        response.headers = {}
        response.json.return_value = {"message": "Too many requests"}
        assert ReadwiseReaderAPIClient._parse_retry_after(response) is None

    def test_malformed_header_falls_through(self):
        response = MagicMock()
        response.headers = {"Retry-After": "not-a-number"}
        response.json.return_value = {}
        assert ReadwiseReaderAPIClient._parse_retry_after(response) is None


# ── _find_document_by_id ──────────────────────────────────────────


class TestFindDocumentById:
    def test_finds_in_first_location(self, api):
        docs = [{"id": "doc1", "summary": "hello"}, {"id": "doc2", "summary": "world"}]
        api.list_documents = MagicMock(return_value=docs)
        result = api._find_document_by_id("doc1", location="feed")
        assert result == docs[0]
        api.list_documents.assert_called_once_with(location="feed")

    def test_falls_back_to_unscoped_search(self, api):
        scoped_docs = [{"id": "other"}]
        unscoped_docs = [{"id": "doc1", "summary": "found"}]
        api.list_documents = MagicMock(side_effect=[scoped_docs, unscoped_docs])
        result = api._find_document_by_id("doc1", location="feed")
        assert result == unscoped_docs[0]
        assert api.list_documents.call_count == 2

    def test_returns_none_when_not_found(self, api):
        api.list_documents = MagicMock(return_value=[{"id": "other"}])
        result = api._find_document_by_id("nonexistent", location=None)
        assert result is None
        api.list_documents.assert_called_once_with(location=None)


# ── mark_for_delete ───────────────────────────────────────────────


class TestMarkForDelete:
    def test_adds_delete_tag(self, api):
        api.update_tags = MagicMock()
        api.update_location = MagicMock()
        api._update = MagicMock()
        result = api.mark_for_delete("doc1", reason="test", tags=["existing"])
        assert result is True
        api.update_tags.assert_called_once_with("doc1", ["existing", "delete"])
        api.update_location.assert_called_once_with("doc1", "later")

    def test_does_not_duplicate_delete_tag(self, api):
        api.update_tags = MagicMock()
        api.update_location = MagicMock()
        api._update = MagicMock()
        api.mark_for_delete("doc1", tags=["delete", "other"])
        api.update_tags.assert_called_once_with("doc1", ["delete", "other"])

    def test_skips_if_already_marked(self, api):
        api._cache_state.set("delete_doc1", True)
        api.update_tags = MagicMock()
        api.update_location = MagicMock()
        api._update = MagicMock()
        result = api.mark_for_delete("doc1", tags=["tag1"])
        assert result is True
        api.update_tags.assert_not_called()
        api.update_location.assert_not_called()
        api._update.assert_not_called()

    def test_no_tags_skips_tag_update(self, api):
        api.update_tags = MagicMock()
        api.update_location = MagicMock()
        api._update = MagicMock()
        api.mark_for_delete("doc1", tags=None)
        api.update_tags.assert_not_called()
        api.update_location.assert_called_once_with("doc1", "later")

    def test_filters_none_tags(self, api):
        api.update_tags = MagicMock()
        api.update_location = MagicMock()
        api._update = MagicMock()
        api.mark_for_delete("doc1", tags=["valid", None, "also_valid"])
        api.update_tags.assert_called_once_with(
            "doc1", ["valid", "also_valid", "delete"]
        )

    def test_tag_failure_continues_with_location(self, api):
        api.update_tags = MagicMock(side_effect=ReadwiseReaderError("tags failed"))
        api.update_location = MagicMock()
        api._update = MagicMock()
        result = api.mark_for_delete("doc1", reason="test", tags=["tag1"])
        assert result is True
        api.update_location.assert_called_once_with("doc1", "later")

    def test_location_failure_invalidates_cache_and_raises(self, api):
        api.update_tags = MagicMock()
        api.update_location = MagicMock(side_effect=ReadwiseReaderError("rate limited"))
        api._update = MagicMock()
        with pytest.raises(ReadwiseReaderError, match="rate limited"):
            api.mark_for_delete("doc1", reason="test", tags=["tag1"])
        assert api._cache_state.get("delete_doc1") is None

    def test_partial_failure_does_not_cache_state(self, api):
        """If tags fail but location succeeds, do not cache the mark."""
        api.update_tags = MagicMock(side_effect=ReadwiseReaderError("tags failed"))
        api.update_location = MagicMock()
        api._update = MagicMock()
        result = api.mark_for_delete("doc1", reason="test", tags=["tag1"])
        assert result is True
        assert api._cache_state.get("delete_doc1") is None


# ── is_marked_for_delete ──────────────────────────────────────────


class TestIsMarkedForDelete:
    def test_returns_true_when_cached(self, api):
        api._cache_state.set("delete_doc1", True)
        assert api.is_marked_for_delete("doc1") is True

    def test_returns_false_when_not_cached(self, api):
        assert api.is_marked_for_delete("doc1") is False


# ── delete_document ───────────────────────────────────────────────


class TestDeleteDocument:
    def test_success(self, api):
        response = MagicMock()
        response.status_code = 200
        api._request = MagicMock(return_value=response)
        api._invalidate_list_cache = MagicMock()
        result = api.delete_document("doc1")
        assert result is True
        api._invalidate_list_cache.assert_called_once()

    def test_success_clears_state_cache(self, api):
        api._cache_state.set("delete_doc1", True)
        response = MagicMock()
        response.status_code = 200
        api._request = MagicMock(return_value=response)
        api._invalidate_list_cache = MagicMock()
        result = api.delete_document("doc1")
        assert result is True
        assert api._cache_state.get("delete_doc1") is None

    def test_404_string_returns_true(self, api):
        api._request = MagicMock(
            side_effect=ReadwiseReaderError(
                "404 Client Error: Not Found for url: https://example.com/"
            )
        )
        result = api.delete_document("doc1")
        assert result is True

    def test_404_status_code_returns_true(self, api):
        import requests

        response = MagicMock()
        response.status_code = 404
        http_error = requests.HTTPError(response=response)
        err = ReadwiseReaderError("not found")
        err.__cause__ = http_error
        api._request = MagicMock(side_effect=err)
        result = api.delete_document("doc1")
        assert result is True

    def test_rate_limit_error_propagates(self, api):
        api._request = MagicMock(side_effect=RateLimitError(retry_after=30))
        with pytest.raises(RateLimitError):
            api.delete_document("doc1")


# ── update_summary ────────────────────────────────────────────────


class TestUpdateSummary:
    def test_without_prefix(self, api):
        api._find_document_by_id = MagicMock(return_value=None)
        api._update = MagicMock(return_value={"ok": True})
        api.update_summary("doc1", "new summary", prefix=False)
        api._update.assert_called_once_with("doc1", summary="new summary")

    def test_with_prefix_existing(self, api):
        api._find_document_by_id = MagicMock(return_value={"summary": "old content"})
        api._update = MagicMock(return_value={"ok": True})
        api.update_summary("doc1", "new summary", prefix=True)
        api._update.assert_called_once_with(
            "doc1", summary="new summary\n-----\nold content"
        )

    def test_with_prefix_no_existing(self, api):
        api._find_document_by_id = MagicMock(return_value=None)
        api._update = MagicMock(return_value={"ok": True})
        api.update_summary("doc1", "new summary", prefix=True)
        api._update.assert_called_once_with("doc1", summary="new summary")


# ── upsert_summary ────────────────────────────────────────────────


class TestUpsertSummary:
    def test_inserts_when_new(self, api):
        api._find_document_by_id = MagicMock(return_value=None)
        api._update = MagicMock(return_value={"ok": True})
        result = api.upsert_summary("doc1", "new content")
        assert result is True

    def test_skips_when_already_present(self, api):
        api._find_document_by_id = MagicMock(
            return_value={"summary": "existing content"}
        )
        api._update = MagicMock()
        result = api.upsert_summary("doc1", "existing content")
        assert result is False
        api._update.assert_not_called()

    def test_prepends_when_not_present(self, api):
        api._find_document_by_id = MagicMock(return_value={"summary": "old content"})
        api._update = MagicMock(return_value={"ok": True})
        result = api.upsert_summary("doc1", "new content")
        assert result is True
        api._update.assert_called_once_with(
            "doc1", summary="new content\n-----\nold content"
        )


# ── other update helpers ──────────────────────────────────────────


class TestOtherUpdateHelpers:
    def test_update_tags(self, api):
        api._update = MagicMock(return_value={"ok": True})
        result = api.update_tags("doc1", ["tag1", "tag2"])
        api._update.assert_called_once_with("doc1", tags=["tag1", "tag2"])
        assert result == {"ok": True}

    def test_update_location(self, api):
        api._update = MagicMock(return_value={"ok": True})
        result = api.update_location("doc1", "archive")
        api._update.assert_called_once_with("doc1", location="archive")
        assert result == {"ok": True}

    def test_update_title(self, api):
        api._update = MagicMock(return_value={"ok": True})
        result = api.update_title("doc1", "New Title")
        api._update.assert_called_once_with("doc1", title="New Title")
        assert result == {"ok": True}

    def test_get_all_documents_uses_cache(self, api):
        api._paginate_list = MagicMock(return_value=[{"id": "1"}])
        result = api.get_all_documents()
        assert result == [{"id": "1"}]
        api._paginate_list.assert_called_once_with({"limit": "100"})

    def test_clear_list_cache(self, api):
        api._cache_api.set("list_foo", [])
        api.clear_list_cache()
        assert api._cache_api.get("list_foo") is None


# ── bulk_update ──────────────────────────────────────────────────


class TestBulkUpdate:
    def test_sends_bulk_request(self, api):
        response = MagicMock()
        response.json.return_value = {
            "results": [{"success": True}, {"success": True}]
        }
        api._request = MagicMock(return_value=response)
        api._invalidate_list_cache = MagicMock()
        count = api.bulk_update([
            {"id": "doc1", "seen": True},
            {"id": "doc2", "seen": True},
        ])
        assert count == 2
        api._request.assert_called_once()

    def test_chunks_large_batch(self, api):
        response_batch1 = MagicMock()
        response_batch1.json.return_value = {
            "results": [{"success": True} for _ in range(50)]
        }
        response_batch2 = MagicMock()
        response_batch2.json.return_value = {
            "results": [{"success": True} for _ in range(25)]
        }
        api._request = MagicMock(side_effect=[response_batch1, response_batch2])
        api._invalidate_list_cache = MagicMock()
        ids = [f"doc{i}" for i in range(75)]
        updates = [{"id": doc_id, "seen": True} for doc_id in ids]
        count = api.bulk_update(updates)
        assert count == 75
        assert api._request.call_count == 2  # 50 + 25

    def test_empty_list_returns_zero(self, api):
        assert api.bulk_update([]) == 0

    def test_mixed_fields(self, api):
        response = MagicMock()
        response.json.return_value = {
            "results": [{"success": True}]
        }
        api._request = MagicMock(return_value=response)
        api._invalidate_list_cache = MagicMock()
        count = api.bulk_update([
            {"id": "doc1", "location": "archive", "tags": ["tag1"]},
        ])
        assert count == 1
        call_args = api._request.call_args
        assert call_args[1]["json"]["updates"][0]["location"] == "archive"

    def test_mark_seen_uses_bulk_update(self, api):
        api.bulk_update = MagicMock(return_value=3)
        count = api.mark_seen(["doc1", "doc2", "doc3"], seen=True)
        assert count == 3
        api.bulk_update.assert_called_once_with([
            {"id": "doc1", "seen": True},
            {"id": "doc2", "seen": True},
            {"id": "doc3", "seen": True},
        ])

    def test_mark_unseen_uses_bulk_update(self, api):
        api.bulk_update = MagicMock(return_value=2)
        count = api.mark_seen(["doc1", "doc2"], seen=False)
        assert count == 2
        api.bulk_update.assert_called_once_with([
            {"id": "doc1", "seen": False},
            {"id": "doc2", "seen": False},
        ])


# ── _request retry logic ─────────────────────────────────────────


class TestRequestRetry:
    def test_retries_on_timeout(self, api):
        import requests

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep"),
        ):
            mock_req.side_effect = [
                requests.Timeout("timed out"),
                success_response,
            ]
            result = api._request("GET", "https://example.com/api")
            assert result == success_response
            assert mock_req.call_count == 2

    def test_retries_on_connection_error(self, api):
        import requests

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep"),
        ):
            mock_req.side_effect = [
                requests.ConnectionError("connection refused"),
                success_response,
            ]
            result = api._request("GET", "https://example.com/api")
            assert result == success_response
            assert mock_req.call_count == 2

    def test_raises_after_max_retries_on_timeout(self, api):
        import requests

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep"),
        ):
            mock_req.side_effect = requests.Timeout("timed out")
            with pytest.raises(requests.Timeout):
                api._request("GET", "https://example.com/api")
            assert mock_req.call_count == 4  # 1 initial + 3 retries

    def test_retries_on_500_server_error(self, api):
        error_response = MagicMock()
        error_response.status_code = 500
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep"),
        ):
            mock_req.side_effect = [error_response, success_response]
            result = api._request("GET", "https://example.com/api")
            assert result == success_response
            assert mock_req.call_count == 2

    def test_raises_on_429_after_max_retries(self, api):
        from readwise_reader_api.client import RateLimitError

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {}
        error_response.json.return_value = {}

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep"),
        ):
            mock_req.return_value = error_response
            with pytest.raises(RateLimitError):
                api._request("GET", "https://example.com/api")

    def test_retries_on_429_with_retry_after_header(self, api):
        from readwise_reader_api.client import RateLimitError

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.headers = {"Retry-After": "2"}
        error_response.json.return_value = {}

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        with (
            patch("readwise_reader_api.client.requests.request") as mock_req,
            patch("readwise_reader_api.client.time.sleep") as mock_sleep,
        ):
            mock_req.return_value = error_response
            with pytest.raises(RateLimitError):
                api._request("GET", "https://example.com/api")
            mock_sleep.assert_called_with(2)

    def test_raises_readwise_error_for_400(self, api):
        import requests

        api._request = ReadwiseReaderAPIClient._request.__get__(api)
        response = MagicMock()
        response.status_code = 400
        response.raise_for_status.side_effect = requests.HTTPError(response=response)

        with patch(
            "readwise_reader_api.client.requests.request", return_value=response
        ), pytest.raises(ReadwiseReaderError):
            api._request("GET", "https://example.com/api")
