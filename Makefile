# Tarn AI Evaluation development tasks. `make help` lists them.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Pinned in CLAUDE.md "Stack and versions". uv downloads it; the host's python is not used.
export UV_PYTHON := 3.12.14

BACKEND  := backend
FRONTEND := frontend
COMPOSE  := docker compose

.PHONY: help setup up up-gpu down logs test test-integration lint typecheck fmt migrate seed ci

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

setup: ## Install Python and Node dependencies; create .env from .env.example
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && npm ci --no-audit --no-fund
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: ## Start the dev stack (postgres+pgvector, minio, api, worker, frontend)
	$(COMPOSE) up --build -d
	@echo "frontend http://localhost:5173  api http://localhost:8000/docs  minio console http://localhost:9001"

up-gpu: ## Start the dev stack with the NVIDIA GPU given to the worker (see docs/development.md)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml up --build -d

down: ## Stop the dev stack (named volumes are kept; `docker compose down -v` deletes the data)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml down

logs: ## Follow the dev stack logs
	$(COMPOSE) logs -f --tail=100

test: ## Backend pytest (all packages) and frontend vitest
	cd $(BACKEND) && uv run pytest
	cd $(FRONTEND) && npm test

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

migrate: ## Apply database migrations (alembic upgrade head) and enable the tarn_app login
	cd $(BACKEND) && uv run tarn db upgrade && uv run tarn db app-login

seed: ## Load idempotent development seed data -- arrives in P8
	@echo "seed: nothing to seed yet; seed data arrives in P8."

ci: lint typecheck test ## What GitHub Actions runs
