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


def test_v_bug_status_weekly_groups_correctly(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    make_history_snapshot(bug_id=2, seen_at=W1, status_name="Closed")

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status_name, bug_count FROM v_bug_status_weekly "
            "WHERE week_start = %s ORDER BY status_name",
            (W1,),
        )
        rows = cur.fetchall()
    assert rows == [("Closed", 1), ("New", 1)]


def test_v_bug_status_weekly_handles_transition(db_conn, make_history_snapshot):
    # bug 1 in New on W1, then Closed on W2 — by W2 it must NOT show under 'New'
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT week_start, status_name, bug_count FROM v_bug_status_weekly "
            "WHERE week_start IN (%s, %s) ORDER BY week_start, status_name",
            (W1, W2),
        )
        rows = cur.fetchall()
    assert rows == [(W1, "New", 1), (W2, "Closed", 1)]


def _throughput_rows(db_conn, week):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, event_count FROM v_bug_throughput_weekly "
            "WHERE week_start = %s ORDER BY event_type",
            (week,),
        )
        return cur.fetchall()


def test_v_bug_throughput_open_count(db_conn, make_history_snapshot):
    # Two bugs created in W1 (only one snapshot each, both 'New').
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    make_history_snapshot(bug_id=2, seen_at=W1, status_name="New")

    rows = _throughput_rows(db_conn, W1)
    assert ("opened", 2) in rows
    assert not any(et == "closed" for et, _ in rows)


def test_v_bug_throughput_close_count_basic(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)

    assert ("opened", 1) in _throughput_rows(db_conn, W1)
    assert ("closed", 1) in _throughput_rows(db_conn, W2)


def test_v_bug_throughput_reopen_double_counts(db_conn, make_history_snapshot):
    # New(W1) → Closed(W2) → In progress(W3) → Closed(W4): two close events expected.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New",         lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed",      lock_version=2)
    make_history_snapshot(bug_id=1, seen_at=W3, status_name="In progress", lock_version=3)
    make_history_snapshot(bug_id=1, seen_at=W4, status_name="Closed",      lock_version=4)

    assert ("opened", 1) in _throughput_rows(db_conn, W1)
    assert ("closed", 1) in _throughput_rows(db_conn, W2)
    assert ("closed", 1) in _throughput_rows(db_conn, W4)


def test_v_bug_throughput_initially_closed_counts_as_close(db_conn, make_history_snapshot):
    # Bug exists with a single snapshot already in Closed — must count as both open AND close in W1.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="Closed")
    rows = _throughput_rows(db_conn, W1)
    assert ("opened", 1) in rows
    assert ("closed", 1) in rows


def test_v_bug_throughput_consecutive_closed_snapshots_count_once(db_conn, make_history_snapshot):
    # Bug closes at W2; then a metadata-only update arrives at W3 still in Closed
    # (lock_version bumped, status unchanged). Only ONE close event must be counted.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New",    lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)
    make_history_snapshot(bug_id=1, seen_at=W3, status_name="Closed", lock_version=3)

    assert ("closed", 1) in _throughput_rows(db_conn, W2)
    assert not any(et == "closed" for et, _ in _throughput_rows(db_conn, W3))
