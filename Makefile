.PHONY: setup dev serve check check-e2e db-start db-stop web-build

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

# Stable single-process trial server: reload is intentionally limited to `make dev`.
serve: setup web-build db-start
	uv run --locked --env-file .env.local uvicorn papertrail.main:app --timeout-graceful-shutdown 15

check:
	uv sync --locked
	uv run --locked ruff check src scripts tests
	uv run --locked ruff format --check src scripts tests
	npm ci --prefix web
	npm run build --prefix web
	uv run --locked python scripts/check_backend.py
	git diff --check

# Real HTTP and PostgreSQL, with an explicitly offline model in a disposable fixture.
check-e2e: web-build
	uv run --locked python scripts/check_e2e.py --evidence output/e2e/latest.json
