"""Simple file-based cache with optional TTL support."""

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any


class Cache:
    """Simple file-based cache with optional TTL."""

    def __init__(
        self, cache_dir: str | Path | None = None, ttl: int | None = None
    ):
        """Initialize cache.

        Args:
            cache_dir: Directory for cache files. Defaults to
                ~/.cache/readwise_reader_api/
            ttl: Time-to-live in seconds. None = no expiration.
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "readwise_reader_api"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl

    @staticmethod
    def _sanitize_key(key: str) -> str:
        """Sanitize a key for use as a filename.

        Replaces all non-alphanumeric characters (except - and _) with
        underscores. Preserves key prefixes for invalidation support.
        """
        return re.sub(r"[^a-zA-Z0-9_-]", "_", str(key))

    def _get_path(self, key: str) -> Path:
        """Get cache file path for key."""
        return self.cache_dir / f"{self._sanitize_key(key)}.json"

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired."""
        path = self._get_path(key)
        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)

            if (
                self.ttl is not None
                and data.get("timestamp", 0) < time.time() - self.ttl
            ):
                path.unlink()
                return None

            return data["value"]
        except (json.JSONDecodeError, KeyError, OSError, TypeError):
            return None

    def set(self, key: str, value: Any) -> None:
        """Set cached value."""
        path = self._get_path(key)
        data = {
            "timestamp": time.time(),
            "value": value,
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except (OSError, TypeError):
            pass

    def delete_expired(self) -> None:
        """Delete all cache files whose stored timestamp exceeds TTL."""
        if self.ttl is None:
            return
        cutoff = time.time() - self.ttl
        for f in self.cache_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                if data.get("timestamp", 0) < cutoff:
                    with contextlib.suppress(OSError):
                        f.unlink()
            except json.JSONDecodeError:
                with contextlib.suppress(OSError):
                    f.unlink()
            except (OSError, KeyError):
                pass

    def invalidate_key(self, key: str) -> None:
        path = self._get_path(key)
        path.unlink(missing_ok=True)

    def invalidate_prefix(self, prefix: str) -> None:
        safe_prefix = self._sanitize_key(prefix)
        for f in self.cache_dir.glob(f"{safe_prefix}*.json"):
            with contextlib.suppress(OSError):
                f.unlink()

    def invalidate_all(self) -> None:
        for f in self.cache_dir.glob("*.json"):
            with contextlib.suppress(OSError):
                f.unlink()
