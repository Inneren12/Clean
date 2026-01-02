from app.settings import settings
from app.shared.circuit_breaker import CircuitBreaker


stripe_circuit = CircuitBreaker(
    name="stripe",
    failure_threshold=settings.stripe_circuit_failure_threshold,
    recovery_time=settings.stripe_circuit_recovery_seconds,
    window_seconds=settings.stripe_circuit_window_seconds,
    half_open_max_calls=settings.stripe_circuit_half_open_max_calls,
)
