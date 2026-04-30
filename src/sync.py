"""One sync cycle: fetch all bugs, upsert into bugs, record history, soft-delete missing."""
import logging
from collections import Counter

import psycopg

from src import client, db

logger = logging.getLogger(__name__)


def run_sync_cycle(conn: psycopg.Connection) -> dict[str, int]:
    """Run a full sync cycle against `conn`. Commits at the end. Returns per-cycle counters."""
    counts: Counter[str] = Counter()
    seen_ids: list[int] = []

    for wp in client.iter_bugs():
        seen_ids.append(wp["id"])
        status = db.upsert_bug(conn, wp)
        counts[status] += 1
        if db.record_history_if_changed(conn, wp):
            counts["history_recorded"] += 1

    counts["deleted"] = db.mark_unseen_as_deleted(conn, seen_ids)
    counts["fetched"] = len(seen_ids)
    conn.commit()

    logger.info(
        "cycle done: fetched=%d inserted=%d updated=%d unchanged=%d "
        "undeleted=%d history_recorded=%d deleted=%d",
        counts["fetched"], counts["inserted"], counts["updated"],
        counts["unchanged"], counts["undeleted"],
        counts["history_recorded"], counts["deleted"],
    )
    return dict(counts)
