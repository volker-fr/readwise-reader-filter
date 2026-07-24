"""Unit tests for readwise_reader_api/cache.py."""

import json
import time

from readwise_reader_api.cache import Cache


class TestSanitizeKey:
    def test_replaces_special_characters(self):
        result = Cache._sanitize_key('list_{"location": "feed"}')
        assert result.isalnum() or all(c.isalnum() or c in ("_", "-") for c in result)

    def test_preserves_prefix(self):
        a = Cache._sanitize_key("list_abc")
        b = Cache._sanitize_key("list_abc_xyz")
        assert b.startswith(a)

    def test_empty_string(self):
        result = Cache._sanitize_key("")
        assert isinstance(result, str)


class TestCacheGetSet:
    def test_set_and_get(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("key1", {"foo": "bar"})
        assert cache.get("key1") == {"foo": "bar"}

    def test_get_missing_key(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        assert cache.get("nonexistent") is None

    def test_overwrites_existing(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("key1", "first")
        cache.set("key1", "second")
        assert cache.get("key1") == "second"

    def test_complex_value(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        value = [{"id": 1, "tags": ["a", "b"]}, {"id": 2, "nested": {"x": True}}]
        cache.set("complex", value)
        assert cache.get("complex") == value


class TestCacheTTL:
    def test_expired_entry_returns_none(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=1)
        cache.set("key1", "value1")
        # Manually backdate the timestamp
        path = cache._get_path("key1")
        with open(path) as f:
            data = json.load(f)
        data["timestamp"] = time.time() - 10
        with open(path, "w") as f:
            json.dump(data, f)
        assert cache.get("key1") is None

    def test_expired_file_deleted_on_get(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=1)
        cache.set("key1", "value1")
        # Backdate
        path = cache._get_path("key1")
        with open(path) as f:
            data = json.load(f)
        data["timestamp"] = time.time() - 10
        with open(path, "w") as f:
            json.dump(data, f)
        cache.get("key1")
        assert not path.exists()

    def test_no_ttl_never_expires(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("key1", "value1")
        # Backdate
        path = cache._get_path("key1")
        with open(path) as f:
            data = json.load(f)
        data["timestamp"] = time.time() - 999999
        with open(path, "w") as f:
            json.dump(data, f)
        assert cache.get("key1") == "value1"


class TestCacheInvalidation:
    def test_invalidate_key(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("key1", "value1")
        cache.invalidate_key("key1")
        assert cache.get("key1") is None

    def test_invalidate_prefix(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("list_feed", "feed_data")
        cache.set("list_all", "all_data")
        cache.set("other", "other_data")
        cache.invalidate_prefix("list_")
        assert cache.get("list_feed") is None
        assert cache.get("list_all") is None
        assert cache.get("other") == "other_data"

    def test_invalidate_all(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.invalidate_all()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_invalidate_missing_key_no_error(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.invalidate_key("nonexistent")


class TestCacheDeleteExpired:
    def test_deletes_expired_files(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=1)
        cache.set("fresh", "yes")
        cache.set("stale", "no")
        # Backdate stale entry
        path = cache._get_path("stale")
        with open(path) as f:
            data = json.load(f)
        data["timestamp"] = time.time() - 10
        with open(path, "w") as f:
            json.dump(data, f)
        cache.delete_expired()
        assert cache.get("fresh") == "yes"
        assert cache.get("stale") is None

    def test_noop_when_no_ttl(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        cache.set("key", "value")
        cache.delete_expired()
        assert cache.get("key") == "value"

    def test_handles_corrupt_json(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=1)
        # Create a corrupt file manually
        safe_key = cache._sanitize_key("corrupt")
        path = cache.cache_dir / f"{safe_key}.json"
        path.write_text("not valid json {{{")
        # Should not raise
        cache.delete_expired()


class TestCorruptCacheFiles:
    def test_corrupt_json_returns_none(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        safe_key = cache._sanitize_key("bad")
        path = cache.cache_dir / f"{safe_key}.json"
        path.write_text("not valid json")
        assert cache.get("bad") is None

    def test_missing_value_key_returns_none(self, tmp_path):
        cache = Cache(cache_dir=tmp_path, ttl=None)
        safe_key = cache._sanitize_key("novalue")
        path = cache.cache_dir / f"{safe_key}.json"
        path.write_text(json.dumps({"timestamp": time.time()}))
        assert cache.get("novalue") is None
