import asyncio
import time

import anyio
import pytest
from redis.exceptions import ResponseError

from redis.exceptions import RedisError

from app.infra.security import InMemoryRateLimiter, RedisRateLimiter, create_rate_limiter


class FakeRedis:
    def __init__(self) -> None:
        self._scripts: dict[str, str] = {}
        self._zsets: dict[str, dict[str, int]] = {}
        self._sequences: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.evalsha_calls = 0

    async def script_load(self, script: str) -> str:
        sha = f"sha{len(self._scripts) + 1}"
        self._scripts[sha] = script
        return sha

    async def evalsha(self, sha: str, numkeys: int, *args):
        if sha not in self._scripts:
            raise ResponseError("NOSCRIPT No matching script")
        self.evalsha_calls += 1
        return await self._execute_script(numkeys, *args)

    async def eval(self, script: str, numkeys: int, *args):
        return await self._execute_script(numkeys, *args)

    async def flushdb(self):
        async with self._lock:
            self._scripts.clear()
            self._zsets.clear()
            self._sequences.clear()
            self.evalsha_calls = 0

    async def scan(self, cursor: int = 0, match: str | None = None, count: int | None = None):  # noqa: ARG002
        async with self._lock:
            keys = set(self._zsets.keys()) | set(self._sequences.keys())
            if match:
                import fnmatch

                keys = {key for key in keys if fnmatch.fnmatch(key, match)}
        return 0, list(keys)

    async def delete(self, *keys: str):
        async with self._lock:
            removed = 0
            for key in keys:
                if key in self._zsets:
                    self._zsets.pop(key, None)
                    removed += 1
                if key in self._sequences:
                    self._sequences.pop(key, None)
                    removed += 1
            return removed

    async def aclose(self):
        return None

    async def _execute_script(self, numkeys: int, *args):
        async with self._lock:
            keys = args[:numkeys]
            argv = args[numkeys:]
            set_key, seq_key = keys
            limit = int(argv[0])
            window_ms = int(argv[1])
            _ttl_seconds = int(argv[2])

            now_ms = int(time.time() * 1000)
            window_start = now_ms - window_ms

            zset = self._zsets.setdefault(set_key, {})
            for member, score in list(zset.items()):
                if score < window_start:
                    zset.pop(member, None)

            if len(zset) >= limit:
                return 0

            sequence = self._sequences.get(seq_key, 0) + 1
            self._sequences[seq_key] = sequence
            member = f"{now_ms}:{sequence}"
            zset[member] = now_ms

            return 1


@pytest.mark.anyio
async def test_inmemory_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(requests_per_minute=2, cleanup_minutes=1)

    assert await limiter.allow("client-1")
    assert await limiter.allow("client-1")
    assert not await limiter.allow("client-1")


@pytest.mark.anyio
async def test_redis_rate_limiter_blocks_after_limit():
    fake_redis = FakeRedis()
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=1,
        cleanup_minutes=1,
        redis_client=fake_redis,
    )

    assert await limiter.allow("client-2")
    assert not await limiter.allow("client-2")

    await limiter.close()


@pytest.mark.anyio
async def test_redis_rate_limiter_is_atomic_under_concurrency():
    fake_redis = FakeRedis()
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=1,
        cleanup_minutes=1,
        redis_client=fake_redis,
    )

    results: list[bool] = []

    async def make_request(start_event: anyio.Event):
        await start_event.wait()
        results.append(await limiter.allow("client-3"))

    start_event = anyio.Event()
    async with anyio.create_task_group() as tg:
        tg.start_soon(make_request, start_event)
        tg.start_soon(make_request, start_event)
        await anyio.sleep(0)
        start_event.set()

    assert sorted(results) == [False, True]


@pytest.mark.anyio
async def test_redis_rate_limiter_uses_lua_script():
    fake_redis = FakeRedis()
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=2,
        cleanup_minutes=1,
        redis_client=fake_redis,
    )

    assert await limiter.allow("client-4")
    assert fake_redis.evalsha_calls > 0

    await limiter.close()


@pytest.mark.anyio
async def test_redis_rate_limiter_falls_back_to_inmemory_on_redis_errors():
    class BrokenRedis(FakeRedis):
        async def evalsha(self, sha: str, numkeys: int, *args):  # noqa: ARG002
            raise RedisError("boom")

        async def eval(self, script: str, numkeys: int, *args):  # noqa: ARG002
            raise RedisError("boom")

        async def ping(self):
            raise RedisError("boom")

    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=2,
        cleanup_minutes=1,
        redis_client=BrokenRedis(),
        fail_open_seconds=5,
        health_probe_seconds=0.01,
    )

    assert await limiter.allow("client-5")
    assert await limiter.allow("client-5")
    assert not await limiter.allow("client-5")

    # still falling back while redis is unhealthy
    await anyio.sleep(0.02)
    assert not await limiter.allow("client-5")

    await limiter.close()


@pytest.mark.anyio
async def test_redis_rate_limiter_recovers_after_outage():
    class FlakyRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.unhealthy = True

        async def evalsha(self, sha: str, numkeys: int, *args):  # noqa: ARG002
            if self.unhealthy:
                raise RedisError("boom")
            return await super().evalsha(sha, numkeys, *args)

        async def eval(self, script: str, numkeys: int, *args):  # noqa: ARG002
            if self.unhealthy:
                raise RedisError("boom")
            return await super().eval(script, numkeys, *args)

        async def ping(self):
            if self.unhealthy:
                raise RedisError("boom")
            return True

    fake_redis = FlakyRedis()
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=2,
        cleanup_minutes=1,
        redis_client=fake_redis,
        fail_open_seconds=0.2,
        health_probe_seconds=0.01,
    )

    assert await limiter.allow("client-6")
    assert await limiter.allow("client-6")
    assert not await limiter.allow("client-6")

    fake_redis.unhealthy = False
    await anyio.sleep(0.25)

    limiter._fail_open_until = time.monotonic() - 0.1  # noqa: SLF001
    await limiter._fallback.reset()  # type: ignore[attr-defined]

    assert await limiter.allow("client-6")
    assert await limiter.allow("client-6")
    assert not await limiter.allow("client-6")

    await limiter.close()


@pytest.mark.anyio
async def test_rate_limiter_defaults_to_inmemory(monkeypatch):
    class AppSettings:
        redis_url = None
        rate_limit_per_minute = 5
        rate_limit_cleanup_minutes = 1

    limiter = create_rate_limiter(AppSettings())

    assert isinstance(limiter, InMemoryRateLimiter)
    assert await limiter.allow("client-6")


@pytest.mark.anyio
async def test_inmemory_rate_limiter_is_protected_by_lock():
    limiter = InMemoryRateLimiter(requests_per_minute=1, cleanup_minutes=1)

    start = anyio.Event()
    results: list[bool] = []

    async def attempt():
        await start.wait()
        results.append(await limiter.allow("client-7"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(attempt)
        tg.start_soon(attempt)
        await anyio.sleep(0)
        start.set()

    assert sorted(results) == [False, True]
