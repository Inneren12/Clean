# CI environment contract

The application defaults to `APP_ENV=prod`, which enforces strict secret validation (for example, rejecting missing or placeholder `AUTH_SECRET_KEY`, `CLIENT_PORTAL_SECRET`, and `WORKER_PORTAL_SECRET`). CI must override this by setting `APP_ENV=dev` (or `test`) for all pytest and Alembic steps so settings load with the relaxed development defaults. Production deployments should continue to run with `APP_ENV=prod` to keep the guardrails active.
