.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

env: ## create .env from example if missing
	@test -f .env || cp .env.example .env

infra: env ## start brokers and stores only
	docker compose up -d redpanda emqx timescale redis minio console

migrate: infra ## apply SQL migrations
	docker compose run --rm migrate

seed: migrate ## seed 100K vehicles, fleets, chargers, tariffs
	docker compose run --rm seed

preflight: ## refuse to start when the host disk is too full for Docker (it goes read-only)
	@free_gb=$$(df -g . | awk 'NR==2{print $$4}'); \
	if [ "$$free_gb" -lt 15 ]; then echo "only $${free_gb} GB free; need 15 GB for the stack (see docs/benchmarks.md §5)"; exit 1; fi; \
	echo "disk ok: $${free_gb} GB free"

run: env preflight ## full stack + simulator + observability (one command)
	docker compose --profile sim --profile obs up --build -d
	@echo "web http://localhost:3000  api http://localhost:8000/docs  grafana http://localhost:3001  kafka console http://localhost:8090  emqx http://localhost:18083"

obs: env ## observability stack only (prometheus, grafana, loki, tempo, otel)
	docker compose --profile obs up -d

dev-retention: ## laptop disk guard: keep 6 h of telemetry in Timescale
	docker compose exec -T timescale psql -U postgres -d orbit -f - < db/dev_retention.sql

DEMO_SERVICES := redpanda timescale redis topics migrate seed ingest state alert planner api web simulator

demo: env preflight ## UI + live simulation only (no MinIO, EMQX, ML, observability)
	docker compose --profile sim up --build -d $(DEMO_SERVICES)
	@echo "web http://localhost:3000  api http://localhost:8000/docs"

sim: ## run only the simulator against the running stack
	docker compose --profile sim up --build simulator

down: ## stop everything, keep data
	docker compose --profile sim down

nuke: ## stop everything, delete volumes
	docker compose --profile sim down -v

# --- local dev (no containers for app code) ---
py-install: ## install python workspace with uv
	cd services && uv sync --all-packages --dev

test: ## unit tests (python + go + web)
	cd services && uv run pytest -q --cov --cov-report=term-missing --cov-report=xml:../tests/reports/coverage.xml
	cd simulator && go test ./...
	cd web && npm test --silent

test-integration: ## integration tests (Testcontainers)
	cd services && uv run pytest -q -m integration tests/integration

lint: ## ruff + mypy + golangci-lint + eslint
	cd services && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd simulator && go vet ./...
	cd web && npm run lint

load: ## k6 load test against running stack
	k6 run tests/load/api.js

chaos: ## kill the broker mid-stream and prove recovery
	bash tests/chaos/kill_broker.sh

.PHONY: help env demo preflight obs dev-retention infra migrate seed run sim down nuke py-install test test-integration lint load chaos
