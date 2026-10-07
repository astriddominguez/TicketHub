# syntax=docker/dockerfile:1
# Build from the repository root (the uv workspace):
#   docker build -f services/catalog/Dockerfile -t tickethub-catalog .
# One image, several processes: the API (default), the outbox relay, migrations.

# ---------- Stage 1: install dependencies with uv ----------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Only what defines the dependencies first: this layer is reused until they change.
COPY pyproject.toml uv.lock ./
COPY services/catalog/pyproject.toml services/catalog/pyproject.toml
COPY services/booking/pyproject.toml services/booking/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package catalog --no-install-workspace

# ---------- Stage 2: the image that runs ----------
FROM python:3.12-slim AS runtime

# A fixed numeric UID: Kubernetes' runAsNonRoot can only verify a number, not a
# user name ("app" could be root for all it knows).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/services/catalog
COPY --from=builder /app/.venv /app/.venv
COPY services/catalog/ ./

# Static files for the admin, served by whitenoise. Settings need their variables
# to load, so collectstatic gets throwaway values (nothing connects to anything).
RUN DJANGO_SECRET_KEY=build JWT_SIGNING_KEY=build \
    CATALOG_DB_NAME=x CATALOG_DB_USER=x CATALOG_DB_PASSWORD=x \
    CATALOG_REDIS_URL=redis://x RABBITMQ_URL=amqp://x \
    python manage.py collectstatic --noinput --verbosity 0

USER 10001:10001
EXPOSE 8000

# One process with threads, not several worker processes: the Prometheus
# counters live in the process, and every scrape must see all of them.
# To handle more traffic, run more containers (replicas).
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "8", \
     "--forwarded-allow-ips", "*"]
