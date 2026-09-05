.PHONY: setup dev check

setup:
	uv sync --locked
	@test -f .env.local || cp .env.example .env.local

dev: setup
	uv run --locked --env-file .env.local uvicorn papertrail.main:app --reload

check:
	uv sync --locked
	uv run --locked ruff check src scripts
	uv run --locked ruff format --check src scripts
	uv run --locked python scripts/smoke.py
	git diff --check
