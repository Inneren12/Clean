import anyio
import pytest

from app.shared.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


@pytest.mark.anyio
async def test_circuit_opens_after_failures():
    breaker = CircuitBreaker(name="email", failure_threshold=2, recovery_time=0.1, window_seconds=10)

    with pytest.raises(RuntimeError):
        await breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    with pytest.raises(RuntimeError):
        await breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))

    with pytest.raises(CircuitBreakerOpenError):
        await breaker.call(lambda: "ok")

    await anyio.sleep(0.11)
    with pytest.raises(RuntimeError):
        await breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    assert breaker.state == "open"


@pytest.mark.anyio
async def test_circuit_half_open_allows_success_and_closes():
    breaker = CircuitBreaker(name="stripe", failure_threshold=1, recovery_time=0.05)

    with pytest.raises(RuntimeError):
        await breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("fail")))

    await anyio.sleep(0.12)
    result = await breaker.call(lambda: "success")

    assert result == "success"
    assert breaker.state == "closed"
