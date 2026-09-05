.PHONY: setup dev check db-start db-stop web-build

setup:
	uv sync --locked
	npm ci --prefix web
	@test -f .env.local || cp .env.example .env.local

web-build:
	npm run build --prefix web

db-start:
	uv run --locked python scripts/postgres.py start

db-stop:
	uv run --locked python scripts/postgres.py stop

dev: setup web-build db-start
	uv run --locked --env-file .env.local uvicorn papertrail.main:app --reload

check:
	uv sync --locked
	uv run --locked ruff check src scripts tests
	uv run --locked ruff format --check src scripts tests
	npm ci --prefix web
	npm run build --prefix web
	uv run --locked python scripts/check_backend.py
	git diff --check
