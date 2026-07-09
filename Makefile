.DEFAULT_GOAL := help
.PHONY: help seed-employees seed-contracts seed-all \
	up down build dev dev-down dev-build logs ps

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
