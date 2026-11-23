.PHONY: install api web dev test lint bench

install:
	cd backend && uv sync
	cd frontend && npm install

api:
	cd backend && REDLINE_SEED=$(CURDIR)/examples uv run uvicorn redline.api:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 api web

test:
	cd backend && uv run pytest --cov
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run typecheck && npm run lint

bench:
	cd backend && uv run python bench.py
