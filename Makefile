CATALOG := uv run --env-file .env python services/catalog/manage.py

.PHONY: help up down ps manage makemigrations migrate run shell relay run-booking consumer worker beat booking-migrate booking-migration test test-catalog test-booking test-cov lint typecheck format

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# --- Infrastructure ---

up: ## Start infrastructure (Postgres x2, Redis, RabbitMQ, Mailpit)
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

relay: ## Publish catalog outbox messages to RabbitMQ (runs until Ctrl+C)
	$(CATALOG) relay_outbox

# --- Booking (FastAPI) ---

run-booking: ## Start the booking dev server on http://localhost:8001
	uv run --env-file .env uvicorn booking.main:app --app-dir services/booking --reload --port 8001

consumer: ## Consume catalog events from RabbitMQ into booking (Ctrl+C to stop)
	cd services/booking && uv run --env-file ../../.env python -m booking.consumer

worker: ## Run the booking Celery worker (background tasks)
	cd services/booking && uv run --env-file ../../.env celery -A booking.celery_app worker --loglevel=info --without-mingle --without-gossip

beat: ## Run the booking Celery Beat scheduler (only ONE at a time)
	cd services/booking && uv run --env-file ../../.env celery -A booking.celery_app beat --loglevel=info --schedule ../../.celerybeat-schedule

booking-migrate: ## Apply booking migrations (Alembic)
	cd services/booking && uv run --env-file ../../.env alembic upgrade head

booking-migration: ## Create a booking migration: make booking-migration MSG="add x"
	cd services/booking && uv run --env-file ../../.env alembic revision --autogenerate -m "$(MSG)"

# --- Quality ---

test: test-catalog test-booking ## Run all tests

test-catalog: ## Run the catalog tests
	cd services/catalog && uv run --env-file ../../.env pytest

test-booking: ## Run the booking tests (needs booking-db running)
	cd services/booking && uv run --env-file ../../.env pytest

test-cov: ## Run all tests with coverage reports
	cd services/catalog && uv run --env-file ../../.env pytest --cov --cov-report=term
	cd services/booking && uv run --env-file ../../.env pytest --cov --cov-report=term

lint: ## Check code style, common mistakes and types
	uv run ruff check .
	uv run ruff format --check .
	$(MAKE) typecheck

typecheck: ## Check type annotations with mypy
	uv run --env-file .env mypy services/catalog
	uv run --env-file .env mypy services/booking

format: ## Auto-fix lint issues and format the code
	uv run ruff check --fix .
	uv run ruff format .
