import logging
import time
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network
from typing import Deque, Dict, Protocol

import redis.asyncio as redis
from redis.exceptions import RedisError, ResponseError

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


RATE_LIMIT_LUA = r'''
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local ttl_seconds = tonumber(ARGV[3])

local time = redis.call('TIME')
local now_ms = (time[1] * 1000) + math.floor(time[2] / 1000)

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - window_ms)
local current = redis.call('ZCARD', KEYS[1])
if current >= limit then
  return 0
end

local seq = redis.call('INCR', KEYS[2])
local member = tostring(now_ms) .. ':' .. tostring(seq)
redis.call('ZADD', KEYS[1], now_ms, member)
redis.call('EXPIRE', KEYS[1], ttl_seconds)
redis.call('EXPIRE', KEYS[2], ttl_seconds)
return 1
'''


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
        self._script_sha: str | None = None
        self.window_ms = 60_000
        self.ttl_seconds = max(int(self.window_ms / 1000) + 2, self.cleanup_seconds)

    async def allow(self, key: str) -> bool:
        try:
            set_key = self._key(key)
            seq_key = self._seq_key(key)

            allowed = await self._eval_script(set_key, seq_key)
            return bool(allowed)
        except RedisError:
            logger.warning("redis rate limiter unavailable; allowing request")
            return True

    async def reset(self) -> None:
        try:
            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor=cursor, match="rate-limit:*", count=100)
                if keys:
                    await self.redis.delete(*keys)
                if cursor == 0:
                    break
        except RedisError:
            logger.warning("redis rate limiter reset failed")

    async def close(self) -> None:
        try:
            await self.redis.aclose()
        except RedisError:
            logger.warning("redis rate limiter close failed")

    def _key(self, key: str) -> str:
        return f"rate-limit:{key}"

    def _seq_key(self, key: str) -> str:
        return f"rate-limit:{key}:seq"

    async def _eval_script(self, set_key: str, seq_key: str) -> int:
        if not self._script_sha:
            self._script_sha = await self.redis.script_load(RATE_LIMIT_LUA)
        try:
            return await self.redis.evalsha(
                self._script_sha,
                2,
                set_key,
                seq_key,
                self.requests_per_minute,
                self.window_ms,
                self.ttl_seconds,
            )
        except ResponseError as exc:
            if "NOSCRIPT" not in str(exc):
                raise
            self._script_sha = await self.redis.script_load(RATE_LIMIT_LUA)
            return await self.redis.evalsha(
                self._script_sha,
                2,
                set_key,
                seq_key,
                self.requests_per_minute,
                self.window_ms,
                self.ttl_seconds,
            )


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
