from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration from environment variables, validated at startup.

    A missing variable or a wrong type (e.g. a non-numeric port) fails immediately
    with a clear message: the same "fail fast" idea as in the catalog's settings.
    """

    db_name: str = Field(alias="BOOKING_DB_NAME")
    db_user: str = Field(alias="BOOKING_DB_USER")
    db_password: SecretStr = Field(alias="BOOKING_DB_PASSWORD")
    db_host: str = Field(default="localhost", alias="BOOKING_DB_HOST")
    db_port: int = Field(default=5432, alias="BOOKING_DB_PORT")

    # Same key the catalog uses to sign tokens: we only verify them here.
    jwt_signing_key: SecretStr = Field(alias="JWT_SIGNING_KEY")
    jwt_algorithm: str = "HS256"

    reservation_ttl_minutes: int = 10

    # RabbitMQ: snapshots of the catalog's events arrive here (see consumer.py).
    rabbitmq_url: SecretStr = Field(alias="RABBITMQ_URL")
    catalog_events_exchange: str = Field(
        default="catalog.events", alias="CATALOG_EVENTS_EXCHANGE"
    )
    catalog_events_queue: str = Field(
        default="booking.catalog-events", alias="BOOKING_CATALOG_EVENTS_QUEUE"
    )

    # Stripe (test mode). Optional: without keys the service runs, and the payment
    # endpoints answer 503 "not configured" instead of failing at startup.
    stripe_secret_key: SecretStr | None = Field(default=None, alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: SecretStr | None = Field(
        default=None, alias="STRIPE_WEBHOOK_SECRET"
    )
    currency: str = "eur"
    # Where buyers land after paying (the success/cancel pages of this service).
    public_url: str = Field(default="http://localhost:8001", alias="BOOKING_PUBLIC_URL")
    # Signs the codes inside the QR tickets, so fakes can be told apart.
    ticket_signing_key: SecretStr = Field(alias="TICKET_SIGNING_KEY")

    # Email: Mailpit in development (captures everything, delivers nothing).
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    email_from: str = Field(
        default="TicketHub <tickets@tickethub.local>", alias="EMAIL_FROM"
    )

    consumer_metrics_port: int = Field(default=9101, alias="CONSUMER_METRICS_PORT")
    # 127.0.0.1 on a laptop; 0.0.0.0 inside a container so Prometheus can reach it.
    consumer_metrics_addr: str = Field(
        default="127.0.0.1", alias="CONSUMER_METRICS_ADDR"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["console", "json"] = Field(
        default="console", alias="LOG_FORMAT"
    )

    redis_url: str = Field(alias="BOOKING_REDIS_URL")
    # Per buyer: enough for a real person, too few for a bot hoarding tickets.
    reservation_rate_limit: int = 10
    reservation_rate_window_seconds: int = 60
    # Short on purpose: a few seconds of staleness only affects what we *show*.
    availability_cache_seconds: int = 5

    @field_validator("stripe_secret_key", "stripe_webhook_secret", mode="before")
    @classmethod
    def empty_means_not_configured(cls, value: object) -> object:
        # "STRIPE_SECRET_KEY=" in .env is an empty string, not "no key": treat it
        # as missing so the payment endpoints say "not configured" (503).
        return None if value == "" else value

    @property
    def database_url(self) -> str:
        password = self.db_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
