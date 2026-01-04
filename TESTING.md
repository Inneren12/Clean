# Testing Guide

## Local test commands
- Full suite: `make test` (runs pytest with async Postgres-backed tests using settings in `.env`).
- Smoke subset: `pytest -m "smoke"` (DB-backed flows such as bookings/deposits).
- Migration invariants: `pytest -m "migrations"`.
- Targeted modules: `pytest tests/test_estimate_api.py`, etc. Use `PYTEST_ADDOPTS` to pass `-k` selectors.
- Org-scope regressions: `pytest tests/test_org_scope_regression_suite.py` to assert org isolation for finance/payments, IAM resets, exports, and signed photo URLs.
- Operator pack hardening: `pytest tests/test_operator_pack_hardening.py` (non-smoke) to verify RBAC, PII masking, org-scoping, filters, and pagination for queues/timeline endpoints. Payment assertions use the invoice `Payment` model from `app/domain/invoices/db_models.py`.
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
- **Async testing**: Tests use the `anyio` plugin (not `pytest-asyncio`). Sync tests use the `client` fixture (FastAPI TestClient). Async fixtures can be wrapped with `asyncio.run()` to make them usable in sync tests.

## CI expectations
- `.github/workflows/ci.yml` runs lint/unit/integration and migration checks; ensure new tests are deterministic.
- `load-smoke.yml` provides load/smoke guidance; avoid adding long-running benchmarks.
- Migration guardrails: `pytest -m "migrations"` enforces a single Alembic head (`test_alembic_has_single_head`) and upgradeability.
- Migration hygiene: Alembic revision IDs must be unique; if two scripts declare the same `revision`, delete or merge the duplicate before running CI.

## Troubleshooting
- If rate limiter blocks tests, set `RATE_LIMIT_PER_MINUTE` high or disable Redis to use in-memory limiter (`app/infra/security.py`).
- If migrations drift, run `alembic upgrade head` and re-run `pytest -m "migrations"`.
- If `alembic heads` returns more than one revision, create a merge migration (`alembic merge -m "merge alembic heads" heads`) before opening a PR so CI can upgrade cleanly.
- For Stripe/email tests, stub settings are used; ensure secrets are set only when running against real services.
- Storage keys for local photo uploads are canonicalized to `orders/{org_id}/{booking_id}/{photo_id}[.ext]`; smoke/regression suites assert files land under that prefix.
- The `X-Test-Org` header is only honored when `settings.testing` is true or `APP_ENV=dev`; prod-mode tests should expect it to be ignored.
