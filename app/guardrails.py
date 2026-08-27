from collections import defaultdict, deque
import secrets
from threading import RLock
from time import monotonic

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError

from .config import get_settings


class SlidingWindowRateLimiter:
    """Thread-safe in-memory limiter for development and tests."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def check(self, scope: str, subject: str, limit: int, window_seconds: int) -> None:
        if limit <= 0:
            return
        now = monotonic()
        key = f"{scope}:{subject}"
        with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise_rate_limit(max(1, int(events[0] + window_seconds - now) + 1))
            events.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


class RedisSlidingWindowRateLimiter:
    """Atomic sliding-window limiter shared by all service instances."""

    _script = """
local now_parts = redis.call('TIME')
local now_ms = now_parts[1] * 1000 + math.floor(now_parts[2] / 1000)
local window_ms = tonumber(ARGV[1]) * 1000
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - window_ms)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if oldest[2] then retry_ms = math.max(1, tonumber(oldest[2]) + window_ms - now_ms) end
  return {0, math.ceil(retry_ms / 1000)}
end
redis.call('ZADD', KEYS[1], now_ms, tostring(now_ms) .. ':' .. ARGV[3])
redis.call('PEXPIRE', KEYS[1], window_ms)
return {1, 0}
"""

    def __init__(self, url: str, prefix: str) -> None:
        self._client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2, health_check_interval=30)
        self._prefix = prefix.rstrip(":")

    def check(self, scope: str, subject: str, limit: int, window_seconds: int) -> None:
        if limit <= 0:
            return
        allowed, retry_after = self._client.eval(
            self._script, 1, f"{self._prefix}:{scope}:{subject}", window_seconds, limit, secrets.token_hex(12)
        )
        if not allowed:
            raise_rate_limit(max(1, int(retry_after)))

    def reset(self) -> None:
        keys = list(self._client.scan_iter(match=f"{self._prefix}:*", count=500))
        if keys:
            self._client.delete(*keys)


class ConfiguredRateLimiter:
    def __init__(self) -> None:
        self._memory = SlidingWindowRateLimiter()
        self._redis: RedisSlidingWindowRateLimiter | None = None
        self._redis_config: tuple[str, str] | None = None
        self._lock = RLock()

    def _redis_limiter(self, url: str, prefix: str) -> RedisSlidingWindowRateLimiter:
        config = (url, prefix)
        with self._lock:
            if self._redis is None or self._redis_config != config:
                self._redis = RedisSlidingWindowRateLimiter(url, prefix)
                self._redis_config = config
            return self._redis

    def check(self, scope: str, subject: str, limit: int, window_seconds: int) -> None:
        settings = get_settings()
        if not settings.redis_url:
            self._memory.check(scope, subject, limit, window_seconds)
            return
        try:
            self._redis_limiter(settings.redis_url, settings.rate_limit_key_prefix).check(scope, subject, limit, window_seconds)
        except (RedisError, ValueError, OSError) as exc:
            if settings.rate_limit_fail_open:
                self._memory.check(scope, subject, limit, window_seconds)
                return
            raise HTTPException(status_code=503, detail="rate limit service unavailable") from exc

    def reset(self) -> None:
        self._memory.reset()
        if self._redis:
            try:
                self._redis.reset()
            except (RedisError, ValueError, OSError):
                pass

    def ready(self) -> bool:
        settings = get_settings()
        if not settings.redis_url:
            return True
        try:
            return bool(self._redis_limiter(settings.redis_url, settings.rate_limit_key_prefix)._client.ping())
        except (RedisError, ValueError, OSError):
            return False


def raise_rate_limit(retry_after: int) -> None:
    raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": str(retry_after)})


rate_limiter = ConfiguredRateLimiter()
