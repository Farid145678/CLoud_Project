"""
Cache layer backed by ElastiCache Redis.
Falls back to a simple in-process dict locally — no Redis needed for dev.

Usage:
    from app.cache import cache_get, cache_set, cache_delete

    result = cache_get("products:all")
    if result is None:
        result = expensive_db_query()
        cache_set("products:all", result, ttl=300)
"""
from __future__ import annotations
import json
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_USE_REDIS    = os.environ.get("STORAGE_BACKEND") == "s3"
_REDIS_HOST   = os.environ.get("REDIS_HOST", "localhost")
_REDIS_PORT   = int(os.environ.get("REDIS_PORT", "6379"))
_LOCAL_STORE: dict = {}   # fallback for local dev

_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    import redis
    _redis_client = redis.Redis(
        host=_REDIS_HOST,
        port=_REDIS_PORT,
        ssl=True,          # ElastiCache in-transit encryption
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    return _redis_client


def cache_get(key: str) -> Optional[Any]:
    if not _USE_REDIS:
        entry = _LOCAL_STORE.get(key)
        if entry and entry["expires"] > time.time():
            return entry["value"]
        return None
    try:
        raw = _redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as e:
        log.warning("Redis GET failed for key=%s: %s", key, e)
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    if not _USE_REDIS:
        _LOCAL_STORE[key] = {"value": value, "expires": time.time() + ttl}
        return
    try:
        _redis().setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        log.warning("Redis SET failed for key=%s: %s", key, e)


def cache_delete(key: str) -> None:
    if not _USE_REDIS:
        _LOCAL_STORE.pop(key, None)
        return
    try:
        _redis().delete(key)
    except Exception as e:
        log.warning("Redis DELETE failed for key=%s: %s", key, e)


def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching a pattern e.g. 'products:*'."""
    if not _USE_REDIS:
        keys_to_del = [k for k in _LOCAL_STORE if k.startswith(pattern.replace("*", ""))]
        for k in keys_to_del:
            _LOCAL_STORE.pop(k)
        return
    try:
        keys = _redis().keys(pattern)
        if keys:
            _redis().delete(*keys)
    except Exception as e:
        log.warning("Redis DELETE pattern=%s failed: %s", pattern, e)
