from __future__ import annotations

import hashlib
import json
from threading import RLock
from time import monotonic
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .config import get_settings


class ResponseCache:
    def __init__(self) -> None:
        self._memory: dict[str, tuple[float, str]] = {}
        self._lock = RLock()
        self._redis: Redis | None = None
        self._redis_url = ""

    def key(self, payload: dict[str, Any], *, tenant: str | None = None) -> str:
        if tenant:
            payload = {"tenant": tenant, **payload}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "loktoken:response:" + hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        settings = get_settings()
        if not settings.cache_enabled:
            return None
        try:
            if settings.redis_url:
                if self._redis is None or self._redis_url != settings.redis_url:
                    self._redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
                    self._redis_url = settings.redis_url
                raw = self._redis.get(key)
                return json.loads(raw) if raw else None
            with self._lock:
                item = self._memory.get(key)
                if not item or item[0] <= monotonic():
                    self._memory.pop(key, None)
                    return None
                return json.loads(item[1])
        except (RedisError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        settings = get_settings()
        if not settings.cache_enabled:
            return
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        try:
            if settings.redis_url:
                if self._redis is None or self._redis_url != settings.redis_url:
                    self._redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
                    self._redis_url = settings.redis_url
                self._redis.setex(key, settings.cache_ttl_seconds, encoded)
            else:
                with self._lock:
                    self._memory[key] = (monotonic() + settings.cache_ttl_seconds, encoded)
        except (RedisError, OSError):
            return


response_cache = ResponseCache()
