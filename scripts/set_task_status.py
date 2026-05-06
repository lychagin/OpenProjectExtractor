"""Set OpenProject WP status by name.

Usage:
  python scripts/set_task_status.py 6857 'In progress'
  python scripts/set_task_status.py 6857 Review
  python scripts/set_task_status.py 6857 Closed

Uses op_client.update_status — handles lockVersion + status name lookup
internally. Prints final status to stdout.
"""
from __future__ import annotations

import argparse
import sys

from op_client import update_status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('wp_id', type=int, help='Work package id (e.g., 6857)')
    ap.add_argument('status', help='Status name (case-insensitive, e.g. "In progress", "Review")')
    args = ap.parse_args()

    updated = update_status(args.wp_id, args.status)
    new_status = updated.get('_links', {}).get('status', {}).get('title')
    print(f'WP#{args.wp_id} → status: {new_status}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
