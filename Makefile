.DEFAULT_GOAL := help
.PHONY: help seed-employees seed-contracts seed-all \
	up down build dev dev-down dev-build logs ps \
	wipe fresh-start

# Override on the command line (make seed-employees PYTHON=python3) if your
# system's plain `python3` lacks a working pip/psycopg2 install.
PYTHON ?= $(shell command -v python3.11 2>/dev/null || command -v python3)

DEV_COMPOSE := docker compose -f docker-compose.yml -f docker-compose.dev.yml

help:
	@echo "Federated Analytics Platform"
	@echo ""
	@echo "  make up          Start the PROD stack (docker compose up -d --build)"
	@echo "  make down        Stop the prod stack"
	@echo "  make build       Rebuild prod images only"
	@echo ""
	@echo "  make dev         Start the DEV stack — hot reload via air/vite (docker-compose.dev.yml)"
	@echo "  make dev-down    Stop the dev stack"
	@echo "  make dev-build   Rebuild dev images only"
	@echo ""
	@echo "  make ps / logs   Status / follow logs for whichever stack is running"
	@echo ""
	@echo "  make seed-employees   Seed Postgres (departments/employees/performance_reviews)"
	@echo "                        and MongoDB (tasks/employee_profiles) via scripts/seed-data.py"
	@echo "  make seed-contracts   Seed a single Elasticsearch contracts index via scripts/seed-elasticsearch.sh"
	@echo "  make seed-all         Run both of the above"
	@echo ""
	@echo "  make wipe             DESTRUCTIVE: stop the prod stack and delete every PV"
	@echo "                        (postgres-meta-data, postgres-source-data, mongo-source-data,"
	@echo "                        elasticsearch-data) — asks for confirmation first"
	@echo "  make fresh-start      wipe + rebuild + start the prod stack + seed-all, for testing"
	@echo "                        the full flow end to end. After it finishes, open the UI and"
	@echo "                        click 'Sync Catalogs' to register datasets/schemas and kick off"
	@echo "                        the ai-engine's enrichment pipeline (column profiling + pgvector"
	@echo "                        schema-RAG embeddings), which runs within one poll interval (~5-35s)."
	@echo ""
	@echo "NOTE: prod and dev use distinct image tags (fap-*:dev for dev), so switching"
	@echo "between 'make up' and 'make dev' never reuses a stale image from the other mode."
	@echo "Always use these targets (or pass --build yourself) instead of a bare"
	@echo "'docker compose up' after code changes — without --build, Compose reuses"
	@echo "whatever image already has that tag, which may be out of date."

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

dev:
	$(DEV_COMPOSE) up -d --build

dev-down:
	$(DEV_COMPOSE) down

dev-build:
	$(DEV_COMPOSE) build

ps:
	docker compose ps

logs:
	docker compose logs -f

seed-employees:
	$(PYTHON) -m pip install -q -r scripts/requirements.txt
	$(PYTHON) scripts/seed-data.py

seed-contracts:
	./scripts/seed-elasticsearch.sh

seed-all: seed-employees seed-contracts

# Stops the prod stack and deletes its named volumes (postgres-meta-data,
# postgres-source-data, mongo-source-data, elasticsearch-data), wiping the
# metadata DB, both source DBs, and the ES indices. `docker compose down -v`
# only removes volumes declared in the resolved config, so with no dev overlay
# file this touches exactly those four — it won't reach dev-only volumes
# (core-api-gomod-cache etc.) that only exist under docker-compose.dev.yml.
wipe:
	@echo "⚠️  This will STOP the stack and PERMANENTLY DELETE all data volumes:"
	@echo "     postgres-meta-data postgres-source-data mongo-source-data elasticsearch-data"
	@read -p "Type 'yes' to continue: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted."; exit 1)
	docker compose down -v --remove-orphans

# Full reset for testing the whole pipeline from an empty install: wipe every
# PV, rebuild images, bring the prod stack up and wait for every healthcheck
# to pass, then seed both source DBs + Elasticsearch. Registering datasets
# (Sync Catalogs in the UI) and the vector/RAG reindex are left as a manual
# next step since they're triggered live, not by a make target — see `make
# help` for what to click and how long to wait.
fresh-start: wipe
	docker compose up -d --build --wait
	$(MAKE) seed-all
	@echo ""
	@echo "✅ Fresh stack up and seeded. Next: open http://localhost:3000, go to Data"
	@echo "   Sources, and click 'Sync Catalogs' to discover postgres_source/mongodb/"
	@echo "   elasticsearch, register their datasets+columns, and trigger the ai-engine's"
	@echo "   enrichment pipeline (profiling + pgvector embeddings), which lands within"
	@echo "   one poll interval (~5-35s) after the sync."
