# Agent Invest — Makefile
# Usage: make <target>
SHELL := /bin/bash

.PHONY: help dev dev-ollama dev-monitoring dev-backend dev-frontend dev-local test test-all eval lint format docker-up docker-down docker-logs mlflow k8s-apply k8s-delete k8s-status k8s-logs env

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: install-backend install-frontend  ## Install all dependencies

install-backend:  ## Install Python backend dependencies via uv
	cd backend && uv pip install -e ".[dev]"

install-frontend:  ## Install Node frontend dependencies
	cd frontend && npm install

dev:  ## Start all services via Docker Compose (OpenAI provider)
	cd infra && docker compose up --build

dev-ollama:  ## Start all services + Ollama (zero API cost)
	cd infra && docker compose --profile ollama up --build

dev-monitoring:  ## Start services + MLflow + Prometheus + Grafana
	cd infra && docker compose --profile monitoring up --build

dev-backend:  ## Run backend only (uvicorn + venv)
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:  ## Run frontend Vite dev server
	cd frontend && npm run dev

dev-local:  ## Run backend + frontend locally (no Docker needed)
	@echo "Starting backend on :8000 and frontend on :5173 ..."
	@(cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000) &
	@cd frontend && npm run dev

test:  ## Run backend unit tests
	cd backend && source .venv/bin/activate && python -m pytest tests/unit -v

test-all:  ## Run all tests
	cd backend && source .venv/bin/activate && python -m pytest -v

eval:  ## Run offline RAGAS evaluation against golden dataset
	cd backend && source .venv/bin/activate && python ../scripts/run_evals.py

migrate:  ## Apply database migrations
	cd backend && source .venv/bin/activate && alembic upgrade head

lint:  ## Run ruff linter on backend
	cd backend && source .venv/bin/activate && ruff check app tests

format:  ## Format backend code with ruff
	cd backend && source .venv/bin/activate && ruff format app tests

docker-up:  ## Start Docker Compose services (detached)
	cd infra && docker compose up -d

docker-down:  ## Stop all Docker Compose services
	cd infra && docker compose down

docker-logs:  ## Tail logs for all services
	cd infra && docker compose logs -f

mlflow:  ## Open MLflow UI in browser
	open http://localhost:5000

k8s-apply:  ## Apply all K8s manifests
	kubectl apply -f infra/k8s/

k8s-delete:  ## Delete all K8s resources in agent-invest namespace
	kubectl delete -f infra/k8s/

k8s-status:  ## Show pod status in agent-invest namespace
	kubectl get pods -n agent-invest

k8s-logs:  ## Tail backend pod logs
	kubectl logs -n agent-invest -l app=backend -f

env:  ## Copy .env.example to .env
	cp .env.example .env
	@echo ".env created — add your API keys before running"

