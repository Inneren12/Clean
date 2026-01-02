# Resilience and circuit breaker policies

## Redis rate limiting
- **Policy:** Fail-open with bounded in-memory fallback. When Redis is unavailable, the limiter switches to a locked in-memory limiter for up to `RATE_LIMIT_FAIL_OPEN_SECONDS` (default 300s) and periodically probes Redis (`RATE_LIMIT_REDIS_PROBE_SECONDS`, default 5s). When Redis responds again, the limiter resets the fallback window and resumes primary enforcement.
- **Why:** Keeps the product available during short Redis outages while still enforcing per-minute limits in a best-effort, deterministic way.
- **Signals:** `app.rate_limit` warnings (`redis rate limiter unavailable; using in-memory fallback`) and recovery info logs, plus Prometheus metrics from `circuit_state` are emitted by the circuit breaker helpers.
- **Tests:** `tests/test_rate_limiter.py` exercises fail-open activation and recovery.

## Stripe circuit breaker
- **Policy:** All outbound Stripe calls flow through a circuit breaker (`stripe_circuit`). Defaults: failure threshold 5, recovery 30s, window 60s, half-open 2. When the breaker is open, API handlers surface `503`/`502` as before but without hammering Stripe.
- **Tests:** `tests/test_resilience_circuits.py::test_stripe_circuit_breaker_opens_and_closes` verifies open/half-open/close cycles using a fake Stripe SDK.

## Email circuit breaker
- **Policy:** Existing email breaker validated and kept. Defaults: failure threshold 5, recovery 30s.
- **Tests:** `tests/test_resilience_circuits.py::test_email_circuit_breaker_opens_and_recovers` proves the breaker opens and later closes after successful probe.

## S3 circuit breaker
- **Policy:** S3 storage backend now wraps operations with a circuit breaker (defaults: threshold 4, recovery 20s, window 60s). If the breaker is open, storage calls fail fast with `CircuitBreakerOpenError`; successful calls reset the breaker.
- **Tests:** `tests/test_resilience_circuits.py::test_s3_circuit_breaker_guards_failures` validates open/close behavior with a fake S3 client.

## Configuration knobs
- Redis: `RATE_LIMIT_FAIL_OPEN_SECONDS`, `RATE_LIMIT_REDIS_PROBE_SECONDS`
- Stripe: `STRIPE_CIRCUIT_FAILURE_THRESHOLD`, `STRIPE_CIRCUIT_RECOVERY_SECONDS`, `STRIPE_CIRCUIT_WINDOW_SECONDS`, `STRIPE_CIRCUIT_HALF_OPEN_MAX_CALLS`
- S3: `S3_CIRCUIT_FAILURE_THRESHOLD`, `S3_CIRCUIT_RECOVERY_SECONDS`, `S3_CIRCUIT_WINDOW_SECONDS`
