"""Destructive integration test: only for dedicated, local cicd_lab_test DB."""

import asyncio
import os
import subprocess
import sys
from urllib.parse import urlparse

import pytest
from sqlalchemy import text

from app.db_async import make_engine


async def scalar(sql: str):
    engine = make_engine()
    try:
        async with engine.begin() as conn:
            return await conn.scalar(text(sql))
    finally:
        await engine.dispose()


async def execute(sql: str) -> None:
    engine = make_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    os.getenv("CICD_MIGRATION_TEST") != "1",
    reason="Enable explicitly with CICD_MIGRATION_TEST=1 on a disposable local DB",
)
def test_legacy_data_resume_and_old_writer_sync():
    url = os.environ.get("DATABASE_URL", "")
    parsed = urlparse(url)
    assert parsed.scheme == "postgresql+asyncpg", "Use asyncpg for this lab"
    assert parsed.path == "/cicd_lab_test", "Refusing non-test database"
    assert parsed.hostname in {"localhost", "127.0.0.1"}, "Use local PostgreSQL only"
    assert parsed.port in {5432, 5433}, "Unexpected PostgreSQL port"
    assert parsed.username == "lab", "Use the disposable lab database account"

    def cli(*args):
        subprocess.run([sys.executable, *args], check=True)

    # WARNING: this removes the disposable test schema. Never run against real DBs.
    cli("-m", "alembic", "downgrade", "base")
    cli("-m", "alembic", "upgrade", "0001_customers")
    asyncio.run(
        execute(
            "INSERT INTO customers(full_name) "
            "SELECT 'Customer ' || n FROM generate_series(1, 10) AS n"
        )
    )

    cli("-m", "alembic", "upgrade", "head")
    assert (
        asyncio.run(scalar("SELECT count(*) FROM customers WHERE display_name IS NULL"))
        == 10
    )

    # Two committed batches, then controlled stop, followed by a fresh process.
    cli(
        "-m",
        "app.backfill_display_name",
        "--batch-size",
        "3",
        "--pause-ms",
        "0",
        "--max-batches",
        "2",
    )
    assert (
        asyncio.run(scalar("SELECT count(*) FROM customers WHERE display_name IS NULL"))
        == 4
    )
    assert asyncio.run(scalar("SELECT status FROM migration_runs")) == "paused"

    # Emulate Blue updating the legacy field. DB trigger maintains alignment.
    asyncio.run(execute("UPDATE customers SET full_name='Updated by Blue' WHERE id=1"))
    assert (
        asyncio.run(scalar("SELECT display_name FROM customers WHERE id=1"))
        == "Updated by Blue"
    )

    cli("-m", "app.backfill_display_name", "--batch-size", "3", "--pause-ms", "0")
    assert (
        asyncio.run(scalar("SELECT count(*) FROM customers WHERE display_name IS NULL"))
        == 0
    )
    assert (
        asyncio.run(
            scalar(
                "SELECT count(*) FROM customers WHERE display_name IS DISTINCT FROM full_name"
            )
        )
        == 0
    )
    assert asyncio.run(scalar("SELECT status FROM migration_runs")) == "completed"
    assert asyncio.run(scalar("SELECT processed_rows FROM migration_runs")) == 10
