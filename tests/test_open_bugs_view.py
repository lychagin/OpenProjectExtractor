"""Integration tests for migration 004 — module columns, backfill, v_open_bugs.

Run with `pytest --integration` (or `make test-integration`). Every mutation
here is rolled back by the db_conn fixture, so this is safe against a
populated database.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_migrations_are_rerunnable(db_conn):
    """Every migration must survive being re-run — bootstrap_schema does exactly
    that on every container start.

    Regression: adding columns to `bugs` broke 002's
    `CREATE OR REPLACE VIEW v_bugs AS SELECT *, ... AS is_closed`, because the
    new columns shift is_closed out of the position the existing view has it in.
    Postgres rejects that with "cannot change name of view column".
    """
    migrations = sorted(
        (Path(__file__).resolve().parent.parent / "db" / "migrations").glob("*.sql")
    )
    assert migrations, "no migration files found"
    with db_conn.cursor() as cur:
        for _ in range(2):
            for path in migrations:
                cur.execute(path.read_text(encoding="utf-8"))
