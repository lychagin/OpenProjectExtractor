import sys
from datetime import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

# Make modules under src/ importable as top-level (matches the pre-existing
# tests/test_client.py style — they `import client`).
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

load_dotenv()


def pytest_addoption(parser):
    parser.addoption(
        "--integration", action="store_true", default=False,
        help="Run integration tests (requires a running Postgres reachable via .env).",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: test requires a running Postgres; skipped unless --integration is passed",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason="integration test (pass --integration to run)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def db_conn():
    """Yield a Postgres connection where every test mutation is rolled back at the end.

    bootstrap_schema commits its DDL (idempotent), but every DML the test does runs
    inside a single transaction that we roll back on teardown — keeps any existing
    production data intact, even if integration tests run against the live DB.
    """
    import psycopg
    import db

    conn = psycopg.connect(db.get_dsn())
    try:
        db.bootstrap_schema(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE bugs RESTART IDENTITY CASCADE")
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def make_history_snapshot(db_conn):
    """Insert a synthetic bug + bug_history row at a controlled seen_at and status.

    Use to set up history scenarios for trend-view tests. The bug row is created
    with minimal columns; status_name on the bug row is updated to match the
    latest snapshot status (so v_bugs reflects the same view of the world).
    """
    def _insert(bug_id: int, seen_at: datetime, status_name: str, lock_version: int = 1):
        assert seen_at.tzinfo is not None, "seen_at must be timezone-aware (use datetime(..., tzinfo=timezone.utc))"
        snapshot = {
            "id": bug_id,
            "subject": f"Bug {bug_id}",
            "lockVersion": lock_version,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "_links": {"status": {"href": "/api/v3/statuses/0", "title": status_name}},
        }
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bugs (id, subject, raw, status_name, lock_version) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE "
                "SET status_name = EXCLUDED.status_name, lock_version = EXCLUDED.lock_version",
                (bug_id, f"Bug {bug_id}", Jsonb(snapshot), status_name, lock_version),
            )
            cur.execute(
                "INSERT INTO bug_history (bug_id, lock_version, seen_at, snapshot) "
                "VALUES (%s, %s, %s, %s)",
                (bug_id, lock_version, seen_at, Jsonb(snapshot)),
            )

    return _insert
