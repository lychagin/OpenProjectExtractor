.PHONY: install test test-integration dry-run run-once show-bug up down logs once psql

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
