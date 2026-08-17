from collections import defaultdict, deque
from threading import RLock
from time import monotonic

from fastapi import HTTPException


class SlidingWindowRateLimiter:
    """Small process-local limiter for the single-service TOKEN deployment."""

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
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
            events.append(now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()
