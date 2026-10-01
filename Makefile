CATALOG := uv run --env-file .env python services/catalog/manage.py

.PHONY: help up down ps manage makemigrations migrate run shell

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# --- Infrastructure ---

up: ## Start infrastructure containers (Postgres)
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
