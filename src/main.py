"""Entry point: bootstraps schema and runs the sync loop forever (or once / dry-run)."""
import argparse
import logging
import os
import sys
import time

import psycopg

from src import client, db, sync

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 300


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenProject bug extractor")
    p.add_argument("--once", action="store_true",
                   help="Run a single sync cycle and exit (no loop).")
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch from OpenProject but do not touch the database. "
                        "Useful as a CI smoke test for API connectivity.")
    return p.parse_args(argv)


def _run_dry_run() -> int:
    bugs = client.extract_bugs()
    logger.info("dry-run: fetched %d bugs (no DB writes)", len(bugs))
    return 0 if bugs else 1


def _run_loop(once: bool) -> int:
    interval = int(os.environ.get("SYNC_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    dsn = db.get_dsn()

    with psycopg.connect(dsn) as conn:
        db.bootstrap_schema(conn)
        while True:
            try:
                sync.run_sync_cycle(conn)
            except Exception:
                # Don't kill the container on a transient error — log and try again next tick.
                logger.exception("sync cycle failed")
                conn.rollback()
            if once:
                return 0
            logger.info("sleeping %ds until next cycle", interval)
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.dry_run:
        return _run_dry_run()
    return _run_loop(once=args.once)


if __name__ == "__main__":
    sys.exit(main())
