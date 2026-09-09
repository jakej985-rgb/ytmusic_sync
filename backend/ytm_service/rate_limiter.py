"""
Sliding-window rate limiter for multi-user sensitive operations (Section 28).

Protects sensitive endpoints:
- Login attempts
- Authentication sessions
- Authentication callbacks
- Manual sync / scan
- Playlist operations
- Upload operations
"""

import time
import asyncio
from typing import Dict, List, Tuple, Optional, Callable
from fastapi import HTTPException, Request, Depends
from .config import settings
import logging

logger = logging.getLogger("ytm_sync.rate_limiter")


class RateLimiter:
    """Thread-safe sliding-window in-memory rate limiter."""

    def __init__(self):
        self._history: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()

    async def check_rate_limit(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, int]:
        """
        Check if request is allowed under the rate limit.
        Returns:
            (is_allowed: bool, retry_after_seconds: int)
        """
        # If rate limiting is globally disabled (e.g. in certain unit tests), allow
        if getattr(settings, "rate_limiting_enabled", True) is False:
            return True, 0

        now = time.time()
        cutoff = now - window_seconds

        async with self._lock:
            timestamps = self._history.get(key, [])
            # Evict timestamps outside the window
            timestamps = [ts for ts in timestamps if ts > cutoff]

            if len(timestamps) >= max_requests:
                earliest = timestamps[0]
                retry_after = max(1, int(window_seconds - (now - earliest)))
                self._history[key] = timestamps
                return False, retry_after

            timestamps.append(now)
            self._history[key] = timestamps
            return True, 0

    async def reset(self, key: Optional[str] = None):
        """Reset rate limit history for a key or all keys (useful for testing)."""
        async with self._lock:
            if key:
                self._history.pop(key, None)
            else:
                self._history.clear()


limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Extract client IP respecting reverse proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def rate_limit_dependency(
    max_requests: int,
    window_seconds: int,
    key_prefix: str,
    use_user_id: bool = False
):
    """
    FastAPI dependency generating a rate limit check.
    """
    async def dependency(request: Request):
        if getattr(settings, "rate_limiting_enabled", True) is False:
            return

        if use_user_id:
            # Check if user object is already attached to request state
            user = getattr(request.state, "current_user", None)
            user_key = user.id if user else get_client_ip(request)
            key = f"{key_prefix}:{user_key}"
        else:
            ip = get_client_ip(request)
            key = f"{key_prefix}:{ip}"

        allowed, retry_after = await limiter.check_rate_limit(key, max_requests, window_seconds)
        if not allowed:
            logger.warning(f"Rate limit exceeded for {key} ({max_requests}/{window_seconds}s). Retry after {retry_after}s.")
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )

    return dependency
