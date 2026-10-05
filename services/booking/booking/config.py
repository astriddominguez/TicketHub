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
