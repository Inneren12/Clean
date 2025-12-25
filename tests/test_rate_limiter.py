import pytest
import fakeredis.aioredis

from app.infra.security import InMemoryRateLimiter, RedisRateLimiter


@pytest.mark.anyio
async def test_inmemory_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(requests_per_minute=2, cleanup_minutes=1)

    assert await limiter.allow("client-1")
    assert await limiter.allow("client-1")
    assert not await limiter.allow("client-1")


@pytest.mark.anyio
async def test_redis_rate_limiter_blocks_after_limit():
    fake_redis = fakeredis.aioredis.FakeRedis()
    limiter = RedisRateLimiter(
        "redis://localhost:6379/0",
        requests_per_minute=1,
        cleanup_minutes=1,
        redis_client=fake_redis,
    )

    assert await limiter.allow("client-2")
    assert not await limiter.allow("client-2")

    await limiter.close()
