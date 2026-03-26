# app/response_cache.py
"""
Smart response cache for the chatbot.

Caches processed chat responses to:
1. Avoid redundant OpenAI API calls (saves costs)
2. Return identical questions instantly
3. Handle near-duplicate questions via text normalization
4. Support TTL-based cache expiry for freshness

Cache strategy:
- Key: normalized + synonym-mapped query text
- TTL: 24 hours by default (FAQ content doesn't change often)
- Size: up to 1024 entries (LRU eviction)
- Persists to disk for survival across restarts
"""

import os
import json
import time
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
from threading import Lock
from functools import lru_cache
from collections import OrderedDict

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

DEFAULT_TTL_SECONDS = 60 * 60 * 24      # 24 hours
DEFAULT_MAX_SIZE = 1024                  # Max cached entries (LRU eviction)
CACHE_FILE_NAME = "response_cache.json"  # Persisted on disk

# ─── LRU CACHE WITH TTL ───────────────────────────────────────────────────────

class TTLCache:
    """
    Thread-safe LRU cache with TTL expiry.

    Keys are normalized query hashes.
    Values are full response dictionaries.
    """

    def __init__(self, maxsize: int = DEFAULT_MAX_SIZE, ttl: int = DEFAULT_TTL_SECONDS):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._lock = Lock()

    def _key(self, query: str) -> str:
        """Generate cache key from normalized query."""
        return hashlib.md5(query.encode("utf-8")).hexdigest()

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """Get cached response if still valid."""
        key = self._key(query)
        with self._lock:
            if key not in self._cache:
                return None

            entry = self._cache[key]
            if time.time() > entry["expires_at"]:
                # Expired
                del self._cache[key]
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return entry["response"]

    def set(self, query: str, response: Dict[str, Any]) -> None:
        """Store response in cache."""
        key = self._key(query)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = {
                "query": query,
                "response": response,
                "created_at": time.time(),
                "expires_at": time.time() + self.ttl,
            }

            # Evict LRU if over capacity
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def invalidate(self, query: str) -> None:
        """Remove a specific entry."""
        key = self._key(query)
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def save(self, path: str) -> None:
        """Persist cache to disk."""
        try:
            with self._lock:
                data = {
                    key: entry for key, entry in self._cache.items()
                    if time.time() < entry["expires_at"]  # Only save non-expired
                }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=None)
            logger.debug(f"Cache saved: {len(data)} entries → {path}")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def load(self, path: str) -> int:
        """Load cache from disk. Returns number of entries loaded."""
        if not os.path.exists(path):
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            loaded = 0
            now = time.time()
            with self._lock:
                for key, entry in data.items():
                    if now < entry.get("expires_at", 0):
                        self._cache[key] = entry
                        loaded += 1

            logger.info(f"Cache loaded: {loaded} valid entries from {path}")
            return loaded
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            return 0

    @property
    def size(self) -> int:
        """Number of entries in cache."""
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> Dict:
        """Cache statistics."""
        with self._lock:
            now = time.time()
            valid = sum(1 for e in self._cache.values() if now < e["expires_at"])
            return {
                "total_entries": len(self._cache),
                "valid_entries": valid,
                "expired_entries": len(self._cache) - valid,
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl,
            }


# ─── GLOBAL CACHE INSTANCE ────────────────────────────────────────────────────

_cache: Optional[TTLCache] = None
_cache_file: Optional[str] = None


def init_cache(
    store_dir: str,
    maxsize: int = DEFAULT_MAX_SIZE,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> TTLCache:
    """
    Initialize the global response cache.

    Args:
        store_dir: Directory to persist cache file
        maxsize: Maximum cache entries
        ttl: Time-to-live in seconds

    Returns:
        Initialized TTLCache instance
    """
    global _cache, _cache_file

    _cache = TTLCache(maxsize=maxsize, ttl=ttl)
    _cache_file = os.path.join(store_dir, CACHE_FILE_NAME)

    # Load persisted cache
    if os.path.exists(_cache_file):
        count = _cache.load(_cache_file)
        logger.info(f"✅ Response cache initialized: {count} entries loaded from disk")
    else:
        logger.info(f"✅ Response cache initialized: empty (no persistent file yet)")

    return _cache


def get_cache() -> Optional[TTLCache]:
    """Get the global cache instance."""
    return _cache


def cache_get(query: str) -> Optional[Dict[str, Any]]:
    """
    Get cached response for a query.

    Args:
        query: Normalized query text

    Returns:
        Cached response dict or None
    """
    if _cache is None:
        return None
    return _cache.get(query)


def cache_set(query: str, response: Dict[str, Any]) -> None:
    """
    Store a response in cache.

    Args:
        query: Normalized query text
        response: Response dictionary to cache
    """
    if _cache is None:
        return

    # Don't cache error responses or low-confidence responses
    meta = response.get("_meta", {})
    if meta.get("source") in ("error", "low_confidence"):
        return
    if not response.get("answer"):
        return

    _cache.set(query, response)

    # Periodically save to disk (every 50 writes)
    if _cache.size % 50 == 0 and _cache_file:
        _cache.save(_cache_file)


def cache_invalidate_all() -> None:
    """Clear all cache entries (call after FAQ update)."""
    if _cache:
        _cache.clear()
        if _cache_file:
            try:
                os.remove(_cache_file)
            except Exception:
                pass
        logger.info("Response cache cleared")


def cache_stats() -> Dict:
    """Get cache statistics."""
    if _cache is None:
        return {"enabled": False}
    return {"enabled": True, **_cache.stats}


# ─── QUERY NORMALIZATION FOR CACHE KEY ────────────────────────────────────────

def normalize_for_cache(query: str) -> str:
    """
    Normalize a query for use as a cache lookup key.

    Uses synonym normalization so that:
    - "pH te laag" and "zuurtegraad te laag" map to same key
    - Case insensitive
    - Extra whitespace collapsed

    Args:
        query: Raw user query

    Returns:
        Normalized query string for cache lookup
    """
    if not query:
        return ""

    try:
        from .synonyms import normalize_with_synonyms
        from .utils import normalize_text
        return normalize_text(normalize_with_synonyms(query.lower()))
    except Exception:
        return query.lower().strip()
