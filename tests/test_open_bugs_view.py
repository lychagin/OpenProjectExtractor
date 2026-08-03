"""Integration tests for migration 004 — module columns, backfill, v_open_bugs.

Run with `pytest --integration` (or `make test-integration`). Every mutation
here is rolled back by the db_conn fixture, so this is safe against a
populated database.
"""
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


def _insert(
    conn,
    bug_id,
    *,
    module=("/api/v3/custom_options/64", "Терра - Пользователи"),
    status="In progress",
    type_name="Bug",
    priority="Normal",
    assignee="Исполнитель",
    author="Автор",
    age_days=10,
    deleted=False,
):
    """Insert a bug row directly, leaving module_id/module_name unset.

    The module columns are deliberately NOT written here — that is what
    backfill_bug_modules() is expected to fill in from raw.
    """
    links = {
        "status": {"href": "/api/v3/statuses/7", "title": status},
        "type": {"href": "/api/v3/types/7", "title": type_name},
        "priority": {"href": "/api/v3/priorities/8", "title": priority},
    }
    if module is not None:
        links["customField14"] = {"href": module[0], "title": module[1]}
    raw = {"id": bug_id, "subject": f"Bug {bug_id}", "_links": links}

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bugs (id, subject, raw, type_name, status_name, priority_name,"
            "                  assignee_name, author_name, op_created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
            "         now() - make_interval(days => %s))",
            (
                bug_id, f"Bug {bug_id}", Jsonb(raw), type_name, status, priority,
                assignee, author, age_days,
            ),
        )
        if deleted:
            cur.execute("UPDATE bugs SET deleted_at = now() WHERE id = %s", (bug_id,))


def test_backfill_populates_module_from_raw(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (64, "Терра - Пользователи")


def test_backfill_is_idempotent(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        # Second run must find nothing left to do.
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 0


def test_backfill_leaves_null_when_custom_field_absent(db_conn):
    _insert(db_conn, 1, module=None)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (None, None)


def test_backfill_repairs_a_stale_module(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("UPDATE bugs SET module_name = 'Старое значение' WHERE id = 1")
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT module_name FROM bugs WHERE id = 1")
        assert cur.fetchone()[0] == "Терра - Пользователи"


def test_backfill_ignores_malformed_href(db_conn):
    """customField14.href without a trailing numeric id (e.g. the bare
    collection endpoint) must backfill to module_id IS NULL, not raise.

    src/db.py's _link_id() already degrades gracefully (try/except ValueError
    -> None) for this exact input; the SQL backfill must agree.
    """
    _insert(db_conn, 1, module=("/api/v3/custom_options", "Терра - Пользователи"))
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (None, "Терра - Пользователи")


def test_view_excludes_closed_statuses(db_conn):
    for i, status in enumerate(
        ["In progress", "Closed", "No issue found", "Rejected", "Developed"], start=1
    ):
        _insert(db_conn, i, status=status)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_open_bugs ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == [1, 5]


def test_view_excludes_soft_deleted_and_non_bug_types(db_conn):
    _insert(db_conn, 1)
    _insert(db_conn, 2, deleted=True)
    _insert(db_conn, 3, type_name="Task")
    _insert(db_conn, 4, type_name="Question")
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_open_bugs ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == [1]


def test_view_substitutes_placeholders_for_missing_module_and_assignee(db_conn):
    _insert(db_conn, 1, module=None, assignee=None)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("SELECT module_name, assignee_name FROM v_open_bugs WHERE id = 1")
        assert cur.fetchone() == ("— без модуля —", "— не назначен —")


def test_view_ranks_priorities_by_urgency_not_alphabetically(db_conn):
    for i, priority in enumerate(["Low", "Immediate", "Normal", "High"], start=1):
        _insert(db_conn, i, priority=priority)
    with db_conn.cursor() as cur:
        cur.execute("SELECT priority_name FROM v_open_bugs ORDER BY priority_rank")
        assert [r[0] for r in cur.fetchall()] == ["Immediate", "High", "Normal", "Low"]


def test_view_ranks_unknown_priority_last(db_conn):
    _insert(db_conn, 1, priority="Immediate")
    _insert(db_conn, 2, priority="Какой-то новый приоритет")
    with db_conn.cursor() as cur:
        cur.execute("SELECT priority_rank FROM v_open_bugs WHERE id = 2")
        assert cur.fetchone()[0] == 9


def test_view_buckets_age_in_chronological_order(db_conn):
    cases = [
        (1, 0, "0–7 дней", 0),
        (2, 7, "0–7 дней", 0),
        (3, 8, "8–30 дней", 1),
        (4, 30, "8–30 дней", 1),
        (5, 31, "31–90 дней", 2),
        (6, 90, "31–90 дней", 2),
        (7, 91, "91–180 дней", 3),
        (8, 180, "91–180 дней", 3),
        (9, 181, "больше 180 дней", 4),
    ]
    for bug_id, age, _, _ in cases:
        _insert(db_conn, bug_id, age_days=age)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, age_days, age_bucket, age_bucket_rank"
                    " FROM v_open_bugs ORDER BY id")
        rows = cur.fetchall()
    assert rows == [(bug_id, age, bucket, rank) for bug_id, age, bucket, rank in cases]


def test_view_handles_a_missing_creation_date(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE bugs SET op_created_at = NULL WHERE id = 1")
        cur.execute("SELECT age_days, age_bucket, age_bucket_rank"
                    " FROM v_open_bugs WHERE id = 1")
        assert cur.fetchone() == (None, "неизвестно", 9)


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
