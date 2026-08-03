"""Postgres storage layer: schema bootstrap + upsert / history / reconcile-delete."""
import logging
import os
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def get_dsn() -> str:
    """Build a Postgres DSN from env vars."""
    parts = {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": os.environ.get("POSTGRES_PORT", "5432"),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
    }
    return " ".join(f"{k}={v}" for k, v in parts.items())


def bootstrap_schema(conn: psycopg.Connection) -> None:
    """Apply all migration files in lexicographic order. Idempotent."""
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
        logger.info("applied migration: %s", path.name)
    conn.commit()


def _link_id(link: dict | None) -> int | None:
    if not link or not isinstance(link, dict):
        return None
    href = link.get("href")
    if not href:
        return None
    try:
        return int(href.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        return None


def _link_title(link: dict | None) -> str | None:
    return link.get("title") if isinstance(link, dict) else None


# OpenProject custom field holding "Модуль". Re-verify after any OpenProject
# form-configuration change with:
#   GET /api/v3/work_packages/schemas/13-7  → customField14.name == "Модуль"
MODULE_CF_KEY = "customField14"

_COLUMNS = (
    "id", "subject", "description_md", "start_date", "due_date",
    "op_created_at", "op_updated_at", "lock_version",
    "percentage_done", "estimated_time", "spent_time", "story_points",
    "status_id", "status_name", "priority_id", "priority_name",
    "type_id", "type_name", "project_id", "project_name",
    "author_id", "author_name", "assignee_id", "assignee_name",
    "responsible_id", "responsible_name", "version_id", "version_name",
    "module_id", "module_name",
    "raw",
)


def _wp_to_row(wp: dict[str, Any]) -> dict[str, Any]:
    links = wp.get("_links") or {}
    desc = wp.get("description") if isinstance(wp.get("description"), dict) else {}
    return {
        "id": wp["id"],
        "subject": wp.get("subject") or "",
        "description_md": desc.get("raw") if desc else None,
        "start_date": wp.get("startDate"),
        "due_date": wp.get("dueDate"),
        "op_created_at": wp.get("createdAt"),
        "op_updated_at": wp.get("updatedAt"),
        "lock_version": wp.get("lockVersion"),
        "percentage_done": wp.get("percentageDone"),
        "estimated_time": wp.get("estimatedTime"),
        "spent_time": wp.get("spentTime"),
        "story_points": wp.get("storyPoints"),
        "status_id": _link_id(links.get("status")),
        "status_name": _link_title(links.get("status")),
        "priority_id": _link_id(links.get("priority")),
        "priority_name": _link_title(links.get("priority")),
        "type_id": _link_id(links.get("type")),
        "type_name": _link_title(links.get("type")),
        "project_id": _link_id(links.get("project")),
        "project_name": _link_title(links.get("project")),
        "author_id": _link_id(links.get("author")),
        "author_name": _link_title(links.get("author")),
        "assignee_id": _link_id(links.get("assignee")),
        "assignee_name": _link_title(links.get("assignee")),
        "responsible_id": _link_id(links.get("responsible")),
        "responsible_name": _link_title(links.get("responsible")),
        "version_id": _link_id(links.get("version")),
        "version_name": _link_title(links.get("version")),
        "module_id": _link_id(links.get(MODULE_CF_KEY)),
        "module_name": _link_title(links.get(MODULE_CF_KEY)),
        "raw": Jsonb(wp),
    }


def upsert_bug(conn: psycopg.Connection, wp: dict[str, Any]) -> str:
    """Upsert one bug. Returns one of: 'inserted', 'updated', 'unchanged', 'undeleted'."""
    row = _wp_to_row(wp)
    cols_csv = ", ".join(_COLUMNS)
    placeholders = ", ".join(f"%({c})s" for c in _COLUMNS)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS if c != "id")
    sql = f"""
        INSERT INTO bugs ({cols_csv}, synced_at, deleted_at)
        VALUES ({placeholders}, now(), NULL)
        ON CONFLICT (id) DO UPDATE
            SET {update_set}, synced_at = now(), deleted_at = NULL
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT lock_version, deleted_at IS NOT NULL AS was_deleted FROM bugs WHERE id = %s",
            (wp["id"],),
        )
        prev = cur.fetchone()
        cur.execute(sql, row)

    if prev is None:
        return "inserted"
    prev_lv, was_deleted = prev
    if was_deleted:
        return "undeleted"
    if prev_lv == wp.get("lockVersion"):
        return "unchanged"
    return "updated"


def record_history_if_changed(conn: psycopg.Connection, wp: dict[str, Any]) -> bool:
    """Insert a snapshot row if lock_version differs from the latest history row. Returns True if inserted."""
    bug_id = wp["id"]
    new_lv = wp.get("lockVersion")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT lock_version FROM bug_history WHERE bug_id = %s ORDER BY seen_at DESC LIMIT 1",
            (bug_id,),
        )
        row = cur.fetchone()
        if row is not None and row[0] == new_lv:
            return False
        cur.execute(
            "INSERT INTO bug_history (bug_id, lock_version, snapshot) VALUES (%s, %s, %s)",
            (bug_id, new_lv, Jsonb(wp)),
        )
    return True


def mark_unseen_as_deleted(conn: psycopg.Connection, seen_ids: Iterable[int]) -> int:
    """Soft-delete bugs that exist in the DB but were not seen in this sync cycle.

    Returns the number of rows newly marked as deleted.
    """
    seen = list(seen_ids)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE bugs SET deleted_at = now() "
            "WHERE deleted_at IS NULL AND NOT (id = ANY(%s))",
            (seen,),
        )
        return cur.rowcount
