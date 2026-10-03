# Tarn AI Evaluation development tasks. `make help` lists them.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Pinned in CLAUDE.md "Stack and versions". uv downloads it; the host's python is not used.
export UV_PYTHON := 3.12.14

BACKEND  := backend
FRONTEND := frontend
COMPOSE  := docker compose

.PHONY: help setup up up-gpu down logs test test-integration test-e2e lint typecheck fmt migrate seed openapi ci

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

setup: ## Install Python and Node dependencies; create .env from .env.example
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && npm ci --no-audit --no-fund
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: ## Start the dev stack (postgres+pgvector, minio, api, worker, frontend)
	@mkdir -p var/keys var/backups  # bind mounts; created by Docker as root otherwise
	$(COMPOSE) up --build -d
	@echo "frontend http://localhost:5173  api http://localhost:8000/docs  minio console http://localhost:9001"

up-gpu: ## Start the dev stack with the NVIDIA GPU given to the worker (see docs/development.md)
	@mkdir -p var/keys var/backups
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml up --build -d

down: ## Stop the dev stack (named volumes are kept; `docker compose down -v` deletes the data)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml down

logs: ## Follow the dev stack logs
	$(COMPOSE) logs -f --tail=100

test: ## Backend pytest (all packages) and frontend vitest
	cd $(BACKEND) && uv run pytest
	cd $(FRONTEND) && npm test

test-e2e: ## Playwright browser tests (API stubbed in the browser; first run: cd frontend && npx playwright install chromium)
	cd $(FRONTEND) && npm run test:e2e

test-integration: ## Tests that need the running stack (make up first)
	cd $(BACKEND) && uv run pytest -m integration

lint: ## ruff check + format --check, import-linter, eslint, prettier --check
	cd $(BACKEND) && uv run ruff check .
	cd $(BACKEND) && uv run ruff format --check .
	cd $(BACKEND) && uv run lint-imports
	cd $(FRONTEND) && npm run lint
	cd $(FRONTEND) && npm run format:check

typecheck: ## mypy --strict and tsc --noEmit
	cd $(BACKEND) && uv run mypy
	cd $(FRONTEND) && npm run typecheck

fmt: ## ruff format and prettier --write
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .
	cd $(FRONTEND) && npm run format

migrate: ## Migrate the application and identity databases; enable the tarn_app and tarn_auth logins
	cd $(BACKEND) && uv run tarn db upgrade && uv run tarn db app-login
	cd $(BACKEND) && uv run tarn identity upgrade && uv run tarn identity app-login

seed: ## Load idempotent development seed data -- arrives in P8
	@echo "seed: nothing to seed yet; seed data arrives in P8."

openapi: ## Write the OpenAPI document to docs/api/openapi.json (R3) and the typed web client from it
	cd $(BACKEND) && uv run python -m tarn_api.openapi
	cd $(FRONTEND) && npm run api:types

ci: openapi lint typecheck test ## What GitHub Actions runs (CI fails if openapi.json is stale)
