from functools import lru_cache

from pydantic import Field, SecretStr
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

    # Email: Mailpit in development (captures everything, delivers nothing).
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    email_from: str = Field(
        default="TicketHub <tickets@tickethub.local>", alias="EMAIL_FROM"
    )

    redis_url: str = Field(alias="BOOKING_REDIS_URL")
    # Per buyer: enough for a real person, too few for a bot hoarding tickets.
    reservation_rate_limit: int = 10
    reservation_rate_window_seconds: int = 60
    # Short on purpose: a few seconds of staleness only affects what we *show*.
    availability_cache_seconds: int = 5

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
