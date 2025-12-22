.PHONY: dev migrate test

dev:
	docker compose up --build

migrate:
	alembic upgrade head

test:
	pytest
