# Lesson 14 — Async PostgreSQL + Alembic + resumable backfill (corrected)

This is the **ASYNC replacement** for the earlier synchronous Lesson 14 bundle.
**Use only this version** if you have not yet implemented the lesson. It uses
`asyncpg`, `sqlalchemy.ext.asyncio.AsyncEngine` and async database operations.

This is additive to the current `cicd-lab` repository. It does not change
`app/main.py`, Dockerfile, GHCR, GitHub OIDC, Nginx, EC2 or live staging.
Don't point test scripts at staging or production databases.

## Copy into the existing repository

- `app/db_async.py`, `app/db_models.py`, `app/backfill_display_name.py` → `app/`
- `db_migrations/` and `alembic.ini` → repo root
- `compose.db.yml` → repo root
- `tests/test_db_migration_backfill.py` → `tests/`
- Append `requirements-additions.txt` dependency lines to your **existing**
  `requirements.txt` (replace psycopg[binary] from the old bundle if previously added).
- `ci/migration-test-job.yml` is a snippet to insert beneath existing workflow `jobs:`.

Do not replace your existing `requirements.txt`, `requirements-dev.txt`,
`.github/workflows/ci.yml`, Dockerfile, or existing tests. Confirm
`requirements-dev.txt` includes `-r requirements.txt`, or ensure the new
dependencies are otherwise installed in your CI job.

## PowerShell (from repo root)

1. `git switch -c feature/postgres-async-migrations`
2. `docker compose -f compose.db.yml up -d`
3. `python -m pip install -r requirements-dev.txt`
4. `$env:DATABASE_URL = 'postgresql+asyncpg://lab:local_only_password@127.0.0.1:5433/cicd_lab_test'`
5. `python -m alembic upgrade 0001_customers`
6. Seed legacy rows:

   `docker exec cicd-lab-postgres-local psql -U lab -d cicd_lab_test -c "INSERT INTO customers(full_name) SELECT 'Customer ' || n FROM generate_series(1, 12) AS n;"`

7. `python -m alembic upgrade head`
8. `python -m app.backfill_display_name --batch-size 3 --pause-ms 0 --max-batches 2`
9. `docker exec cicd-lab-postgres-local psql -U lab -d cicd_lab_test -c "SELECT status, processed_rows FROM migration_runs; SELECT count(*) AS remaining FROM customers WHERE display_name IS NULL;"`
10. `python -m app.backfill_display_name --batch-size 3 --pause-ms 0`
11. Confirm completed, no NULL rows and old-writer trigger:

    `docker exec cicd-lab-postgres-local psql -U lab -d cicd_lab_test -c "UPDATE customers SET full_name='Latest Name' WHERE id=1; SELECT full_name,display_name FROM customers WHERE id=1; SELECT status,processed_rows FROM migration_runs;"`

12. Run disposable local DB test (WARNING: it runs Alembic downgrade base):

    `$env:CICD_MIGRATION_TEST = '1'`

    `python -m pytest -q tests/test_db_migration_backfill.py`

If you already ran the previous bundle on **this disposable database**, your
schema and records may exist. For a clean exercise, first run
`docker compose -f compose.db.yml down -v` **only if you are happy to
permanently delete this lab's PostgreSQL data**, then run `up -d`.

## What changed vs previous bundle

| Component | Old | Async replacement |
|---|---|---|
| Driver | psycopg synchronous connection | asyncpg |
| DB URL | `postgresql+psycopg://` | `postgresql+asyncpg://` |
| FastAPI DB abstraction | No async factory | `AsyncEngine`, `async_sessionmaker` in `app/db_async.py` |
| Alembic | Sync Engine | AsyncEngine + `await connection.run_sync()` |
| Backfill | psycopg sync execute + time.sleep | AsyncConnection + await execute + asyncio.sleep |
| Tests | psycopg sync | SQLAlchemy async/asyncpg against real PostgreSQL |

**Alembic nuance:** migration revision functions (`op.create_table`,
`op.add_column`, etc.) remain synchronous-style functions because that's
Alembic's interface. Alembic executes them via `AsyncConnection.run_sync()`;
its database I/O uses the async engine and asyncpg.

**Async isn't automatically faster.** We retain a small number of committed
updates per batch, short transactions, sleep/throttle, data correctness,
advisory lock, and reconciliation. `asyncio.sleep()` and async I/O won't
avoid PostgreSQL row locks or prevent a heavy migration from saturating the DB.

## CI integration

Copy the `migration-tests` job into the existing `.github/workflows/ci.yml`
under `jobs:`. Change `build.needs` to include `migration-tests`, preserving
other dependencies (for example `needs: [test, migration-tests]`).
Don't create a second deploy job or remove your working blue-green CD steps.
PR runs test against an ephemeral GitHub PostgreSQL service only.

## Safety notes

- `full_name` remains authoritative while old Blue can write it. An INSERT
  / UPDATE OF full_name trigger synchronizes `display_name` for this phase.
  Don't let a new Green version write only `display_name` yet.
- `SKIP LOCKED` may return zero rows while locked/uncommitted work remains;
  the worker performs a separate outstanding-work check before completion.
- PostgreSQL session advisory lock prevents simultaneous copies of this
  worker, including between individual batch commits. It releases if the
  worker's connection ends abruptly.
- The progress counter and data batch update commit atomically.
- `alembic downgrade` is destructive and is never part of app rollback.
- Local password is disposable lab-only. Never expose PostgreSQL publicly.
- This bundle does NOT deploy databases to EC2 or automatically apply schema
  migrations during web-app deployment. That is a later controlled lesson.
- Test code intentionally destroys the `cicd_lab_test` schema; do not run
  in an environment mapped to staging or production.

## Async session example (future FastAPI integration)

```python
from sqlalchemy import select
from app.db_async import make_engine, make_session_factory
from app.db_models import Customer

engine = make_engine()                        # once per app process
SessionLocal = make_session_factory(engine)

async def find_customer(customer_id: int):
    async with SessionLocal() as session:
        return await session.scalar(
            select(Customer).where(Customer.id == customer_id)
        )

# Dispose engine during FastAPI lifespan shutdown: await engine.dispose()
```

SQLAlchemy AsyncSession must NOT be shared concurrently across tasks;
create a new session for each independent request/task.
