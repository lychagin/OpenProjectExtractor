.PHONY: install test test-integration dry-run run-once show-bug up down logs once psql datalens-up datalens-down datalens-logs prod-up prod-down prod-logs

# Compose files used together for the full stack (extractor + Postgres + DataLens).
COMPOSE_FULL := -f docker-compose.yml -f docker-compose.datalens.yml

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
PIP := $(if $(wildcard .venv/bin/pip),.venv/bin/pip,pip)

# If a local CA bundle exists, use it (needed for hosts whose TLS chain is missing intermediates).
CA_BUNDLE := $(wildcard .cert/bundle.pem)
ifneq ($(CA_BUNDLE),)
export REQUESTS_CA_BUNDLE := $(abspath $(CA_BUNDLE))
endif

# --- Host-side (uses .venv) -------------------------------------------------

install:
	$(PIP) install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/ -v

test-integration:
	$(PYTHON) -m pytest tests/ -v --integration

dry-run:
	$(PYTHON) -m src.main --dry-run

run-once:
	$(PYTHON) -m src.main --once

show-bug:
	@if [ -z "$(ID)" ]; then echo "usage: make show-bug ID=<work_package_id>"; exit 2; fi
	@$(PYTHON) -m src.show_bug $(ID)

# --- docker compose stack ---------------------------------------------------

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f extractor

once:
	docker compose run --rm extractor --once

psql:
	docker compose exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

# --- DataLens stack ---------------------------------------------------------
# Heavy: 9 extra services (~3-5GB images, ~2-4GB RAM). Brings up everything
# (extractor + Postgres + DataLens) so DataLens can reach the bugs DB by
# hostname `postgres:5432` over the shared compose network.

datalens-up:
	docker compose $(COMPOSE_FULL) up -d

datalens-down:
	docker compose $(COMPOSE_FULL) down

datalens-logs:
	docker compose $(COMPOSE_FULL) logs -f ui ui-api us

# --- Production stack (extractor pulled from ghcr.io + DataLens + nginx) ----
COMPOSE_PROD := -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml

prod-up:
	docker compose $(COMPOSE_PROD) up -d

prod-down:
	docker compose $(COMPOSE_PROD) down

prod-logs:
	docker compose $(COMPOSE_PROD) logs -f extractor nginx
