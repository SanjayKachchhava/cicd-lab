"""Resumable, asyncpg-backed demo backfill for the expand-contract migration.

Run ONLY after `alembic upgrade head`. Full_name remains authoritative, and
the migration trigger synchronizes old-version writes. Each batch and its
progress counter commit atomically in their own short transaction.
"""
import argparse
import asyncio

from sqlalchemy import text, pool
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.db_async import database_url

JOB = "backfill_customer_display_name"
ADVISORY_LOCK_ID = 2026111401

BATCH_SQL = text("""
WITH picked AS (
    SELECT id FROM customers
    WHERE display_name IS NULL
    ORDER BY id
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
)
UPDATE customers AS c
SET display_name = c.full_name
FROM picked
WHERE c.id = picked.id
RETURNING c.id
""")


async def set_status(conn: AsyncConnection, status: str) -> None:
    async with conn.begin():
        await conn.execute(text("""
            INSERT INTO migration_runs(name, status, processed_rows)
            VALUES (:name, :status, 0)
            ON CONFLICT(name) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = now(),
                completed_at = CASE WHEN EXCLUDED.status = 'completed'
                    THEN now() ELSE NULL END
        """), {"name": JOB, "status": status})


async def run(batch_size: int, pause_ms: int, max_batches: int | None) -> int:
    if batch_size <= 0 or pause_ms < 0 or (max_batches is not None and max_batches <= 0):
        raise ValueError("batch-size > 0, pause-ms >= 0, max-batches > 0")

    # One connection for the entire worker; the PostgreSQL session advisory
    # lock survives individual COMMITs and is released on disconnect.
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            async with conn.begin():
                locked = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": ADVISORY_LOCK_ID},
                )
            if not locked:
                raise RuntimeError("Another backfill worker already holds the advisory lock")

            try:
                await set_status(conn, "running")
                batches = 0
                idle_polls = 0

                while True:
                    async with conn.begin():
                        result = await conn.execute(BATCH_SQL, {"batch_size": batch_size})
                        changed = len(result.fetchall())
                        if changed:
                            await conn.execute(text("""
                                UPDATE migration_runs
                                SET processed_rows = processed_rows + :changed,
                                    updated_at = now()
                                WHERE name = :name
                            """), {"changed": changed, "name": JOB})

                    if changed:
                        batches += 1
                        idle_polls = 0
                        print(f"committed batch={batches} rows={changed}", flush=True)
                        if max_batches is not None and batches >= max_batches:
                            await set_status(conn, "paused")
                            print("Paused for lab. Restart to resume remaining rows.")
                            return 0
                        if pause_ms:
                            await asyncio.sleep(pause_ms / 1000)
                        continue

                    # A SKIP LOCKED batch may return zero even when rows remain.
                    # Inspect the outstanding work in a separate transaction.
                    async with conn.begin():
                        remaining = await conn.scalar(text("""
                            SELECT EXISTS (
                                SELECT 1 FROM customers WHERE display_name IS NULL
                            )
                        """))
                        inconsistent = False
                        if not remaining:
                            inconsistent = await conn.scalar(text("""
                                SELECT EXISTS (
                                    SELECT 1 FROM customers
                                    WHERE display_name IS DISTINCT FROM full_name
                                )
                            """))

                        if not remaining and not inconsistent:
                            await conn.execute(text("""
                                UPDATE migration_runs
                                SET status='completed',
                                    updated_at=now(), completed_at=now()
                                WHERE name=:name
                            """), {"name": JOB})

                    if not remaining and not inconsistent:
                        print("Backfill completed: no NULL or inconsistent display_name rows")
                        return 0
                    if inconsistent:
                        await set_status(conn, "blocked")
                        raise RuntimeError("Found inconsistent rows; investigate before completion")

                    idle_polls += 1
                    if idle_polls >= 10:
                        await set_status(conn, "blocked")
                        raise RuntimeError("Unprocessed locked rows remain; retry later")
                    await asyncio.sleep(1)
            finally:
                # Session-level locks also release automatically if connection
                # drops, e.g., after SIGKILL/VM crash. Do not mask an exception
                # if explicit cleanup fails because the connection was lost.
                try:
                    async with conn.begin():
                        await conn.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": ADVISORY_LOCK_ID},
                        )
                except Exception:
                    pass
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--pause-ms", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=None,
                        help="Lab only: stop after N committed batches")
    args = parser.parse_args()
    return asyncio.run(run(args.batch_size, args.pause_ms, args.max_batches))


if __name__ == "__main__":
    raise SystemExit(main())
