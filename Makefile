# Task runner for the whole repo. Backend targets run through uv inside backend/.

.PHONY: install test lint format typecheck check infra-up infra-down infra-ps

infra-up:
	docker compose up -d

infra-down:
	docker compose down

infra-ps:
	docker compose ps

worker:
	cd backend && uv run celery -A orchestrator.worker.celery_app worker --pool=solo --loglevel=info

api:
	cd backend && uv run uvicorn orchestrator.api.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

install:
	cd backend && uv sync

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check src tests

format:
	cd backend && uv run ruff format src tests

typecheck:
	cd backend && uv run mypy src tests

check: lint typecheck test
