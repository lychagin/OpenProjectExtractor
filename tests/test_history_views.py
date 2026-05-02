"""Integration tests for db/migrations/003_history_views.sql.

Run with `pytest --integration` (or `make test-integration`).
"""
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


# ISO Mondays (UTC) used as week-bucket anchors throughout the test suite.
W1 = datetime(2026, 4, 27, tzinfo=timezone.utc)  # Monday
W2 = W1 + timedelta(weeks=1)
W3 = W1 + timedelta(weeks=2)
W4 = W1 + timedelta(weeks=3)


def test_make_history_snapshot_creates_bug_and_history_row(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bugs WHERE id = 1")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM bug_history WHERE bug_id = 1")
        assert cur.fetchone()[0] == 1


def test_is_status_closed_function(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT is_status_closed('Closed')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('No issue found')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('Rejected')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('In progress')")
        assert cur.fetchone()[0] is False
        cur.execute("SELECT is_status_closed('Tested')")
        assert cur.fetchone()[0] is False  # narrow definition — Tested is NOT closed
