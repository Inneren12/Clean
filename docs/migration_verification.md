# Migration verification (PostgreSQL)

This checklist shows how to validate Alembic migrations 0035–0039 on a real PostgreSQL instance and run the automated invariants.

## 1) Start PostgreSQL locally

Use the existing docker-compose service:

```bash
docker compose up -d postgres
```

The service exposes port `5432` and seeds a `cleaning` database with the `postgres` user.

## 2) Point the app to the local database

Set `DATABASE_URL` so Alembic and the tests use the running container:

```bash
export DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:5432/cleaning"
```

If you already have existing data in that database, create a throwaway database name in the URL instead (for example `cleaning_test_migrations`).

## 3) Run the migrations end-to-end

Apply the migrations against the configured database:

```bash
alembic upgrade head
```

## 4) Run the migration invariants suite

Execute the automated checks that create an isolated database, run `alembic upgrade head`, and validate the org_id and uniqueness constraints:

```bash
pytest tests/test_postgres_migrations.py -q
```

The suite verifies:

- org_id columns are NOT NULL and have organization foreign keys on core tables
- the email dedupe and unsubscribe unique constraints exist
- `stripe_events` has the expected `org_id` column and foreign key

## 5) Tear down

When finished, stop the local PostgreSQL container:

```bash
docker compose down
```
