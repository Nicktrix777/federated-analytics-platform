.DEFAULT_GOAL := help
.PHONY: help seed-employees seed-contracts seed-all

# Override on the command line (make seed-employees PYTHON=python3) if your
# system's plain `python3` lacks a working pip/psycopg2 install.
PYTHON ?= $(shell command -v python3.11 2>/dev/null || command -v python3)

help:
	@echo "Federated Analytics Platform — data seeding"
	@echo ""
	@echo "  make seed-employees   Seed Postgres (departments/employees/performance_reviews)"
	@echo "                        and MongoDB (tasks/employee_profiles) via scripts/seed-data.py"
	@echo "  make seed-contracts   Seed a single Elasticsearch contracts index via scripts/seed-elasticsearch.sh"
	@echo "  make seed-all         Run both of the above"
	@echo ""
	@echo "Requires the stack to be running (docker compose up -d) and reachable on"
	@echo "localhost at the ports in docker-compose.yml."

seed-employees:
	$(PYTHON) -m pip install -q -r scripts/requirements.txt
	$(PYTHON) scripts/seed-data.py

seed-contracts:
	./scripts/seed-elasticsearch.sh

seed-all: seed-employees seed-contracts
