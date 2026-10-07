CATALOG := uv run --env-file .env python services/catalog/manage.py

.PHONY: help install up stack stack-logs down ps manage makemigrations migrate run shell relay run-booking consumer worker beat stripe-listen booking-migrate booking-migration k8s-up k8s-cluster k8s-images k8s-secret k8s-deploy k8s-status k8s-down tf-init tf-plan tf-apply tf-destroy loadtest-seed loadtest loadtest-ui loadtest-check loadtest-clean test test-catalog test-booking test-cov lint typecheck format

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install every dependency of the whole workspace (run after uv add/remove)
	uv sync --all-packages

# --- Infrastructure ---

INFRA := catalog-db booking-db redis rabbitmq mailpit jaeger prometheus grafana

up: ## Start ONLY the infrastructure (run the services yourself: make run...)
	docker compose up -d $(INFRA)

stack: ## Start the WHOLE system in containers, behind http://localhost:8080
	docker compose up -d --build

stack-logs: ## Follow the logs of the application containers
	docker compose logs -f catalog catalog-relay booking booking-consumer booking-worker booking-beat gateway

down: ## Stop all containers (data is kept)
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

stripe-listen: ## Forward Stripe test webhooks to booking (needs the Stripe CLI)
	stripe listen --forward-to localhost:8001/webhooks/stripe --events checkout.session.completed

booking-migrate: ## Apply booking migrations (Alembic)
	cd services/booking && uv run --env-file ../../.env alembic upgrade head

booking-migration: ## Create a booking migration: make booking-migration MSG="add x"
	cd services/booking && uv run --env-file ../../.env alembic revision --autogenerate -m "$(MSG)"

# --- Load testing (needs `make run` and `make run-booking` running) ---

LOADTEST := PYTHONPATH=services/booking uv run --env-file .env

loadtest-seed: ## Create the load-test event (id 900001) in the booking database
	$(LOADTEST) python loadtests/seed.py

loadtest: loadtest-seed ## Ticket rush: 300 users for 60 s, then check nothing was oversold
	mkdir -p loadtests/results
	@# The oversell check runs even if Locust saw failures; the run still
	@# fails if either of them did.
	uv run --env-file .env locust -f loadtests/locustfile.py --headless \
		--users 300 --spawn-rate 30 --run-time 60s \
		--csv loadtests/results/run --html loadtests/results/report.html; \
	status=$$?; $(MAKE) --no-print-directory loadtest-check && exit $$status

loadtest-ui: loadtest-seed ## Interactive load test at http://localhost:8089
	uv run --env-file .env locust -f loadtests/locustfile.py

loadtest-check: ## Verify held + available == total for every load-test zone
	$(LOADTEST) python loadtests/seed.py --check

loadtest-clean: ## Delete the load-test event and its reservations
	$(LOADTEST) python loadtests/seed.py --clean

# --- Kubernetes (local cluster with kind + Helm) ---

K8S_CLUSTER := tickethub
CHART := infra/k8s/charts/tickethub

k8s-up: k8s-cluster k8s-images k8s-secret k8s-deploy ## Create the cluster and deploy everything (http://localhost:8090)

k8s-cluster: ## Create the local kind cluster (3 nodes)
	kind get clusters | grep -qx $(K8S_CLUSTER) || kind create cluster --name $(K8S_CLUSTER) --config infra/k8s/kind-config.yaml

k8s-images: ## Build the images and load them into the cluster's nodes
	docker compose build catalog booking
	kind load docker-image tickethub-catalog:local tickethub-booking:local --name $(K8S_CLUSTER)

k8s-secret: ## Create/refresh the Kubernetes secret from your .env (never stored in git)
	kubectl create secret generic tickethub-env --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -

k8s-deploy: ## Install or upgrade the Helm release and wait until it's ready
	helm upgrade --install tickethub $(CHART) --wait --timeout 10m

k8s-status: ## Show what's running in the cluster
	kubectl get pods,svc,jobs -o wide

k8s-down: ## Delete the whole local cluster
	kind delete cluster --name $(K8S_CLUSTER)

# --- Terraform (declares the same cluster + release as code) ---

TF := terraform -chdir=infra/terraform

tf-init: ## Download the Terraform providers (once)
	$(TF) init

tf-plan: ## Show what Terraform would change, without changing anything
	$(TF) plan

tf-apply: ## Create/update cluster, images, secret and release (http://localhost:8090)
	$(TF) apply

tf-destroy: ## Delete everything Terraform created
	$(TF) destroy

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
