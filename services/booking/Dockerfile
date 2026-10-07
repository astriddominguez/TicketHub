# syntax=docker/dockerfile:1
# Build from the repository root (the uv workspace):
#   docker build -f services/booking/Dockerfile -t tickethub-booking .
# One image, several processes: the API (default), the RabbitMQ consumer, the
# Celery worker and beat, and migrations (alembic).

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
    uv sync --frozen --no-dev --package booking --no-install-workspace

# ---------- Stage 2: the image that runs ----------
FROM python:3.12-slim AS runtime

# A fixed numeric UID: Kubernetes' runAsNonRoot can only verify a number, not a
# user name ("app" could be root for all it knows).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/services/booking
COPY --from=builder /app/.venv /app/.venv
COPY services/booking/ ./

USER 10001:10001
EXPOSE 8001

# One worker process (Prometheus counters live in the process); more traffic =
# more containers. Behind the gateway the app lives under /booking (root-path),
# and the client's IP comes from the gateway's X-Forwarded-For (proxy-headers).
CMD ["uvicorn", "booking.main:app", \
     "--host", "0.0.0.0", "--port", "8001", \
     "--root-path", "/booking", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-keep-alive", "75"]
