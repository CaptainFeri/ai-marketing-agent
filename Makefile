.PHONY: help install lint format typecheck test test-cov migrate revision run worker beat db-up db-down analyze schemas

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create a virtualenv and install the project with dev extras
	uv venv .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

lint:  ## Static checks
	.venv/bin/ruff check app tests migrations

format:  ## Apply formatting and import ordering
	.venv/bin/ruff format app tests migrations
	.venv/bin/ruff check --fix app tests migrations

typecheck:  ## Type check the application package
	.venv/bin/mypy app

test:  ## Run the test suite (needs PostgreSQL; see scripts/dev_postgres.sh)
	.venv/bin/pytest -q

test-cov:  ## Run the suite with a coverage report
	.venv/bin/pytest --cov=app --cov-report=term-missing

migrate:  ## Apply migrations
	.venv/bin/alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add x"
	.venv/bin/alembic revision --autogenerate -m "$(m)"

run:  ## Run the API with reload
	.venv/bin/uvicorn app.main:app --reload --port 8000

worker:  ## Run the GPU worker (concurrency 1 — the card has one owner)
	.venv/bin/celery -A app.worker.celery_app worker -Q gpu -c 1 -l INFO

beat:  ## Run the periodic scheduler
	.venv/bin/celery -A app.worker.celery_app beat -l INFO

db-up:  ## Start a local PostgreSQL for development and tests
	./scripts/dev_postgres.sh start

db-down:  ## Stop it
	./scripts/dev_postgres.sh stop

analyze:  ## Probe this machine and print the configuration it implies
	.venv/bin/python scripts/analyze_platform.py --benchmark

schemas:  ## Regenerate schemas/ from app/agents/contracts.py
	.venv/bin/python scripts/export_schemas.py
