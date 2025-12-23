.PHONY: dev up down logs wait-db migrate revision psql test

WAIT_DB_COMMAND = docker compose exec -T postgres pg_isready -U postgres -d cleaning

dev:
	docker compose up --build

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

wait-db:
	@echo "Waiting for postgres to be ready..."
	@until $(WAIT_DB_COMMAND) >/dev/null 2>&1; do \
		sleep 1; \
	done

migrate: wait-db
	docker compose exec -T api alembic upgrade head

revision:
	docker compose exec -T api alembic revision --autogenerate -m "$(msg)"

psql:
	docker compose exec -T postgres psql -U postgres -d cleaning

test:
	pytest
