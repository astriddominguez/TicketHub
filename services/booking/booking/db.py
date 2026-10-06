from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from booking.config import get_settings


class Base(DeclarativeBase):
    # Predictable constraint/index names, so Alembic migrations can refer to them
    # (and so error messages tell you which rule was broken).
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# One engine (connection pool) for the whole process.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    async with SessionFactory() as session:
        yield session


@asynccontextmanager
async def standalone_session() -> AsyncIterator[AsyncSession]:
    """A session with its own short-lived engine, for code outside the web app.

    Celery tasks run each job in a fresh event loop (asyncio.run). Pooled asyncpg
    connections belong to the loop that created them, so the shared `engine`
    can't be reused there: open one connection, use it, close it.
    """
    task_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(task_engine, expire_on_commit=False) as session:
            yield session
    finally:
        await task_engine.dispose()
