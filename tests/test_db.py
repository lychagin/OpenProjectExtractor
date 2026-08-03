"""Integration tests for src/db.py against a real Postgres.

Run with `pytest --integration` (or `make test-integration`). Requires the
docker-compose stack up (or any other Postgres reachable via the env vars
read by db.get_dsn()).
"""
import pytest

import db

pytestmark = pytest.mark.integration


def _wp(id: int = 1, lock_version: int = 1, **overrides):
    base = {
        "id": id,
        "subject": f"Bug {id}",
        "lockVersion": lock_version,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "description": {"format": "markdown", "raw": "desc", "html": "<p>desc</p>"},
        "_links": {
            "status":   {"href": "/api/v3/statuses/7", "title": "In progress"},
            "priority": {"href": "/api/v3/priorities/3", "title": "Low"},
            "type":     {"href": "/api/v3/types/7",     "title": "Bug"},
            "project":  {"href": "/api/v3/projects/13", "title": "Project"},
            "author":   {"href": "/api/v3/users/24",    "title": "Author"},
            "assignee": {"href": "/api/v3/users/22",    "title": "Assignee"},
        },
    }
    base.update(overrides)
    return base


def test_upsert_inserted_then_unchanged_then_updated(db_conn):
    wp = _wp(id=1, lock_version=1)
    assert db.upsert_bug(db_conn, wp) == "inserted"
    assert db.upsert_bug(db_conn, wp) == "unchanged"
    assert db.upsert_bug(db_conn, _wp(id=1, lock_version=2)) == "updated"


def test_upsert_returns_undeleted_after_soft_delete(db_conn):
    wp = _wp(id=42)
    db.upsert_bug(db_conn, wp)
    assert db.mark_unseen_as_deleted(db_conn, []) == 1
    assert db.upsert_bug(db_conn, wp) == "undeleted"
    # And deleted_at should be cleared
    with db_conn.cursor() as cur:
        cur.execute("SELECT deleted_at FROM bugs WHERE id = 42")
        assert cur.fetchone()[0] is None


def test_record_history_skips_when_lock_version_unchanged(db_conn):
    wp = _wp(id=1, lock_version=1)
    db.upsert_bug(db_conn, wp)

    assert db.record_history_if_changed(db_conn, wp) is True
    assert db.record_history_if_changed(db_conn, wp) is False

    wp2 = _wp(id=1, lock_version=2)
    db.upsert_bug(db_conn, wp2)
    assert db.record_history_if_changed(db_conn, wp2) is True

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bug_history WHERE bug_id = 1")
        assert cur.fetchone()[0] == 2


def test_mark_unseen_as_deleted_is_idempotent(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    db.upsert_bug(db_conn, _wp(id=2))
    db.upsert_bug(db_conn, _wp(id=3))

    assert db.mark_unseen_as_deleted(db_conn, [1, 2]) == 1
    # Running again with the same "seen" set must not re-mark already-deleted rows.
    assert db.mark_unseen_as_deleted(db_conn, [1, 2]) == 0


def test_link_fields_are_extracted(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status_id, status_name, priority_id, priority_name, "
            "type_id, type_name, project_id, project_name, "
            "assignee_id, assignee_name "
            "FROM bugs WHERE id = 1"
        )
        row = cur.fetchone()
    assert row == (
        7, "In progress",
        3, "Low",
        7, "Bug",
        13, "Project",
        22, "Assignee",
    )


def test_null_links_become_null_columns(db_conn):
    wp = _wp(id=1)
    wp["_links"]["responsible"] = {"href": None, "title": None}
    wp["_links"]["version"] = {"href": None, "title": None}
    db.upsert_bug(db_conn, wp)
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT responsible_id, responsible_name, version_id, version_name "
            "FROM bugs WHERE id = 1"
        )
        assert cur.fetchone() == (None, None, None, None)


def test_v_bugs_view_hides_soft_deleted(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    db.upsert_bug(db_conn, _wp(id=2))
    db.mark_unseen_as_deleted(db_conn, [1])  # bug id=2 is now soft-deleted

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_bugs ORDER BY id")
        ids = [r[0] for r in cur.fetchall()]
    assert ids == [1]


def test_v_bugs_view_derives_is_closed(db_conn):
    closed_status = {"href": "/api/v3/statuses/9", "title": "Closed"}
    no_issue_status = {"href": "/api/v3/statuses/10", "title": "No issue found"}
    rejected_status = {"href": "/api/v3/statuses/11", "title": "Rejected"}
    open_status = {"href": "/api/v3/statuses/7", "title": "In progress"}

    for i, st in enumerate([closed_status, no_issue_status, rejected_status, open_status], start=1):
        wp = _wp(id=i)
        wp["_links"]["status"] = st
        db.upsert_bug(db_conn, wp)

    with db_conn.cursor() as cur:
        cur.execute("SELECT id, is_closed FROM v_bugs ORDER BY id")
        rows = cur.fetchall()
    # ids 1,2,3 → Closed/No issue found/Rejected → is_closed=True
    # id 4 → In progress → is_closed=False
    assert rows == [(1, True), (2, True), (3, True), (4, False)]


def test_module_columns_are_upserted(db_conn):
    wp = _wp(id=1)
    wp["_links"]["customField14"] = {
        "href": "/api/v3/custom_options/64",
        "title": "Терра - Пользователи",
    }
    db.upsert_bug(db_conn, wp)
    with db_conn.cursor() as cur:
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (64, "Терра - Пользователи")


def test_module_columns_are_null_without_custom_field(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    with db_conn.cursor() as cur:
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (None, None)
