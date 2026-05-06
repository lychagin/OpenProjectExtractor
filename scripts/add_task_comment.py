"""Post a comment to an OpenProject WP activity tab.

Usage:
  # inline comment
  python scripts/add_task_comment.py 6857 --text 'MR: https://... \nshort summary'

  # comment from file
  python scripts/add_task_comment.py 6857 --file /tmp/mr-summary.md

  # comment from stdin
  echo 'MR: https://...' | python scripts/add_task_comment.py 6857 --stdin

Body is treated as Markdown (OpenProject renders it). Useful in /finish-task
for posting MR link + short summary of what changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from op_client import post_comment


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('wp_id', type=int, help='Work package id (e.g., 6857)')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--text', help='Comment body (Markdown)')
    src.add_argument('--file', type=Path, help='Read comment body from file')
    src.add_argument('--stdin', action='store_true', help='Read comment body from stdin')
    args = ap.parse_args()

    if args.text:
        body = args.text
    elif args.file:
        body = args.file.read_text(encoding='utf-8')
    else:
        body = sys.stdin.read()

    body = body.strip()
    if not body:
        sys.exit('Empty comment body')

    result = post_comment(args.wp_id, body)
    activity_id = result.get('id')
    print(f'WP#{args.wp_id} ← comment posted (activity id={activity_id})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
