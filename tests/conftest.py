import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

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
