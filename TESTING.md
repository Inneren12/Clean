# Testing Guide

## Local test commands
- Full suite: `make test` (runs pytest with async Postgres-backed tests using settings in `.env`).
- Smoke subset: `pytest -m "smoke"` (DB-backed flows such as bookings/deposits).
- Migration invariants: `pytest -m "migrations"`.
- Targeted modules: `pytest tests/test_estimate_api.py`, etc. Use `PYTEST_ADDOPTS` to pass `-k` selectors.
- Org-scope regressions: `pytest tests/test_org_scope_regression_suite.py` to assert org isolation for finance/payments, IAM resets, exports, and signed photo URLs.
- Metrics contracts: `pytest tests/test_metrics_endpoint.py -k metrics_path_label` to ensure HTTP metrics use templated route labels.

## Markers
Defined in `pytest.ini`:
- `@pytest.mark.sanity` – fast dependency checks.
- `@pytest.mark.smoke` – high-level flows hitting Postgres.
- `@pytest.mark.postgres` – requires Postgres; often used for async DB tests.
- `@pytest.mark.migrations` – validates Alembic history and schema invariants.

## Database requirements
- Tests expect Postgres reachable via `DATABASE_URL` (Docker compose uses host `postgres`). Alembic migrations create schema before tests.
- Many tests rely on async SQLAlchemy sessions; keep models in sync with `app/infra/models.py` and migrations.

## Fixtures and patterns
- Factories/fixtures live under `tests/` aligning with domain modules; reuse existing helper functions rather than recreating data setup.
- For SaaS-authenticated endpoints, use helpers that mint JWTs via `app/api/routes_auth.py` flows or fixture utilities.
- Use `X-Test-Org` header only in testing mode to set org context when entitlements require it (`app/api/entitlements.py`).

## CI expectations
- `.github/workflows/ci.yml` runs lint/unit/integration and migration checks; ensure new tests are deterministic.
- `load-smoke.yml` provides load/smoke guidance; avoid adding long-running benchmarks.
- Migration guardrails: `pytest -m "migrations"` enforces a single Alembic head (`test_alembic_has_single_head`) and upgradeability.

## Troubleshooting
- If rate limiter blocks tests, set `RATE_LIMIT_PER_MINUTE` high or disable Redis to use in-memory limiter (`app/infra/security.py`).
- If migrations drift, run `alembic upgrade head` and re-run `pytest -m "migrations"`.
- If `alembic heads` returns more than one revision, create a merge migration (`alembic merge -m "merge alembic heads" heads`) before opening a PR so CI can upgrade cleanly.
- For Stripe/email tests, stub settings are used; ensure secrets are set only when running against real services.
