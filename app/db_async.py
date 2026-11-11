"""Async PostgreSQL session factory for FastAPI (not wired to main.py yet).

Create one engine per application process; reuse the factory for requests.
Dispose the engine on application shutdown.
"""
import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("Set DATABASE_URL using postgresql+asyncpg://...")
    return url


def make_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or database_url(), pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
