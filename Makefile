CATALOG := uv run --env-file .env python services/catalog/manage.py

.PHONY: help up down ps manage makemigrations migrate run shell run-booking test test-cov lint typecheck format

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# --- Infrastructure ---

up: ## Start infrastructure containers (Postgres x2)
	docker compose up -d

down: ## Stop infrastructure containers (data is kept)
	docker compose down

ps: ## Show container status
	docker compose ps

# --- Catalog (Django) ---

manage: ## Run any manage.py command: make manage CMD="createsuperuser"
	$(CATALOG) $(CMD)

makemigrations: ## Create catalog migrations
	$(CATALOG) makemigrations

migrate: ## Apply catalog migrations
	$(CATALOG) migrate

run: ## Start the catalog dev server on http://localhost:8000
	$(CATALOG) runserver

shell: ## Open a Django shell for the catalog
	$(CATALOG) shell

# --- Booking (FastAPI) ---

run-booking: ## Start the booking dev server on http://localhost:8001
	uv run --env-file .env uvicorn booking.main:app --app-dir services/booking --reload --port 8001

# --- Quality ---

test: ## Run the catalog tests
	cd services/catalog && uv run --env-file ../../.env pytest

test-cov: ## Run the catalog tests with a coverage report
	cd services/catalog && uv run --env-file ../../.env pytest --cov --cov-report=term

lint: ## Check code style, common mistakes and types
	uv run ruff check .
	uv run ruff format --check .
	$(MAKE) typecheck

typecheck: ## Check type annotations with mypy
	uv run --env-file .env mypy services/catalog services/booking

format: ## Auto-fix lint issues and format the code
	uv run ruff check --fix .
	uv run ruff format .
