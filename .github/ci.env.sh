#!/usr/bin/env bash
# Writes the .env used in CI: throwaway values for the CI service containers.
# None of these are real secrets. Real ones never go in the repository.
set -euo pipefail

cat > .env <<'EOF'
CATALOG_DB_NAME=catalog
CATALOG_DB_USER=catalog
CATALOG_DB_PASSWORD=ci-password
CATALOG_DB_HOST=localhost
CATALOG_DB_PORT=5433

DJANGO_SECRET_KEY=ci-only-not-a-real-secret-key-0123456789abcdef
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver

JWT_SIGNING_KEY=ci-only-jwt-signing-key-0123456789abcdef0123456789

BOOKING_DB_NAME=booking
BOOKING_DB_USER=booking
BOOKING_DB_PASSWORD=ci-password
BOOKING_DB_HOST=localhost
BOOKING_DB_PORT=5434

REDIS_PORT=6379
CATALOG_REDIS_URL=redis://localhost:6379/1
BOOKING_REDIS_URL=redis://localhost:6379/0

RABBITMQ_USER=tickethub
RABBITMQ_PASSWORD=ci-password
RABBITMQ_PORT=5672
RABBITMQ_URL=amqp://tickethub:ci-password@localhost:5672/

SMTP_HOST=localhost
SMTP_PORT=1025
EMAIL_FROM="TicketHub <tickets@tickethub.local>"

STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
BOOKING_PUBLIC_URL=http://localhost:8001
TICKET_SIGNING_KEY=ci-only-ticket-signing-key-0123456789abcdef

LOG_FORMAT=json
LOG_LEVEL=INFO

# No OTEL_EXPORTER_OTLP_ENDPOINT in CI: no collector, so tracing isn't set up
# (tests that check spans use their own in-memory tracer).
EOF
echo "CI .env written"
