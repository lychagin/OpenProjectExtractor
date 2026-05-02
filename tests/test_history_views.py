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


def test_bug_state_at_returns_latest_before_t(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="In progress", lock_version=2)
    make_history_snapshot(bug_id=1, seen_at=W3, status_name="Closed", lock_version=3)

    with db_conn.cursor() as cur:
        for t, expected in [(W1, "New"), (W2, "In progress"), (W3, "Closed")]:
            cur.execute("SELECT status_name FROM bug_state_at(%s) WHERE bug_id = 1", (t,))
            assert cur.fetchone()[0] == expected, f"at t={t}"


def test_bug_state_at_excludes_future(db_conn, make_history_snapshot):
    # snapshot exists in the future; querying earlier must return zero rows for this bug
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed")
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bug_state_at(%s)", (W1 - timedelta(seconds=1),))
        assert cur.fetchone()[0] == 0


def test_bug_state_at_empty_history(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bug_state_at(now())")
        assert cur.fetchone()[0] == 0


def test_bug_state_at_multiple_bugs_independent(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New",    lock_version=1)
    make_history_snapshot(bug_id=2, seen_at=W1, status_name="Closed", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT bug_id, status_name FROM bug_state_at(%s) ORDER BY bug_id", (W1,),
        )
        rows = cur.fetchall()
    assert rows == [(1, "New"), (2, "Closed")]
