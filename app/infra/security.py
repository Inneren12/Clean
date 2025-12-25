import logging
import time
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network
from typing import Deque, Dict, Protocol

import redis.asyncio as redis
from redis.exceptions import RedisError

from starlette.requests import Request


logger = logging.getLogger("app.rate_limit")


class RateLimiter(Protocol):
    async def allow(self, key: str) -> bool: ...

    async def reset(self) -> None: ...

    async def close(self) -> None: ...


class InMemoryRateLimiter:
    def __init__(self, requests_per_minute: int, cleanup_minutes: int = 10) -> None:
        self.requests_per_minute = requests_per_minute
        self.cleanup_minutes = cleanup_minutes
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_seen: Dict[str, float] = {}
        self._last_prune: float = 0.0

    async def allow(self, key: str) -> bool:
        now = time.time()
        self._maybe_prune(now)
        window_start = now - 60
        timestamps = self._requests[key]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        if len(timestamps) >= self.requests_per_minute:
            self._last_seen[key] = now
            return False
        timestamps.append(now)
        self._last_seen[key] = now
        return True

    async def reset(self) -> None:
        self._requests.clear()
        self._last_seen.clear()
        self._last_prune = 0.0

    async def close(self) -> None:
        return None

    def _maybe_prune(self, now: float) -> None:
        if now - self._last_prune < 60:
            return
        expire_before = now - (self.cleanup_minutes * 60)
        for key in list(self._requests.keys()):
            timestamps = self._requests[key]
            if not timestamps or self._last_seen.get(key, 0.0) < expire_before:
                self._requests.pop(key, None)
                self._last_seen.pop(key, None)
        self._last_prune = now


class RedisRateLimiter:
    def __init__(
        self,
        redis_url: str,
        requests_per_minute: int,
        cleanup_minutes: int = 10,
        redis_client: redis.Redis | None = None,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.cleanup_seconds = max(int(cleanup_minutes * 60), 60)
        self.redis = redis_client or redis.from_url(redis_url, encoding="utf-8", decode_responses=False)

    async def allow(self, key: str) -> bool:
        try:
            now = time.time()
            window_start = now - 60
            set_key = self._key(key)
            seq_key = self._seq_key(key)

            await self.redis.zremrangebyscore(set_key, 0, window_start)
            current = await self.redis.zcard(set_key)
            if current >= self.requests_per_minute:
                return False

            sequence = await self.redis.incr(seq_key)
            member = f"{now}-{sequence}"
            await self.redis.zadd(set_key, {member: now})
            await self.redis.expire(set_key, self.cleanup_seconds)
            await self.redis.expire(seq_key, self.cleanup_seconds)
            return True
        except RedisError:
            logger.warning("redis rate limiter unavailable; allowing request")
            return True

    async def reset(self) -> None:
        try:
            await self.redis.flushdb()
        except RedisError:
            logger.warning("redis rate limiter flush failed")

    async def close(self) -> None:
        try:
            await self.redis.aclose()
        except RedisError:
            logger.warning("redis rate limiter close failed")

    def _key(self, key: str) -> str:
        return f"rate-limit:{key}"

    def _seq_key(self, key: str) -> str:
        return f"rate-limit:{key}:seq"


def create_rate_limiter(app_settings) -> RateLimiter:
    if getattr(app_settings, "redis_url", None):
        return RedisRateLimiter(
            app_settings.redis_url,
            app_settings.rate_limit_per_minute,
            cleanup_minutes=app_settings.rate_limit_cleanup_minutes,
        )
    return InMemoryRateLimiter(
        app_settings.rate_limit_per_minute,
        cleanup_minutes=app_settings.rate_limit_cleanup_minutes,
    )


def resolve_client_key(
    request: Request,
    trust_proxy_headers: bool,
    trusted_proxy_ips: list[str],
    trusted_proxy_cidrs: list[str],
) -> str:
    client_host = request.client.host if request.client else "unknown"
    if not trust_proxy_headers or not _is_trusted_proxy(client_host, trusted_proxy_ips, trusted_proxy_cidrs):
        return client_host
    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return client_host
    first_ip = forwarded_for.split(",")[0].strip()
    try:
        ip_address(first_ip)
    except ValueError:
        return client_host
    return first_ip


def _is_trusted_proxy(client_host: str, trusted_ips: list[str], trusted_cidrs: list[str]) -> bool:
    if client_host in trusted_ips:
        return True
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return False
    for cidr in trusted_cidrs:
        try:
            if client_ip in ip_network(cidr):
                return True
        except ValueError:
            continue
    return False
