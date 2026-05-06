"""Fetch product-sort feature subtasks for a given assignee from OpenProject.

Reads the mapping file produced by create_product_sort_subtasks.py
(output/product_sort_subtasks_created.json — code → WP id) and fetches
fresh data from OpenProject for every WP: subject, status, assignee,
estimated time, lock_version.

Output: JSON list on stdout (and optionally a file via --out), filtered
by --assignee (default: 'Сергей Лычагин').

Usage:
  python scripts/fetch_my_tasks.py
  python scripts/fetch_my_tasks.py --assignee 'Vadim Valiev'
  python scripts/fetch_my_tasks.py --all                # no assignee filter
  python scripts/fetch_my_tasks.py --out output/my_tasks.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENPROJECT_URL = os.getenv('OPENPROJECT_URL', 'https://projects-customdev.wone-it.ru')
TOKEN = os.getenv('OPENPROJECTTOKEN')
CERT_PATH = Path(__file__).resolve().parent.parent / '.cert' / 'bundle.pem'
MAPPING_FILE = Path(__file__).resolve().parent.parent / 'output' / 'product_sort_subtasks_created.json'

DEFAULT_ASSIGNEE = 'Сергей Лычагин'


def auth():
    if not TOKEN:
        sys.exit('OPENPROJECTTOKEN is required (set in .env)')
    return ('apikey', TOKEN)


def headers():
    return {'Accept': 'application/json'}


def fetch_wp(wp_id: int) -> dict:
    r = requests.get(
        f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}',
        auth=auth(), headers=headers(),
        verify=str(CERT_PATH), timeout=30,
    )
    r.raise_for_status()
    return r.json()


def extract(wp: dict, code: str) -> dict:
    links = wp.get('_links', {})
    assignee = links.get('assignee', {}) or {}
    status = links.get('status', {}) or {}
    return {
        'code': code,
        'wp_id': wp['id'],
        'subject': wp.get('subject'),
        'status': status.get('title'),
        'assignee': assignee.get('title'),
        'assignee_href': assignee.get('href'),
        'estimated_time': wp.get('estimatedTime'),
        'lock_version': wp.get('lockVersion'),
        'url': f'{OPENPROJECT_URL}/wp/{wp["id"]}',
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--assignee', default=DEFAULT_ASSIGNEE,
                    help=f'Filter by assignee title (default: {DEFAULT_ASSIGNEE})')
    ap.add_argument('--all', action='store_true', help='Do not filter by assignee')
    ap.add_argument('--out', type=Path, help='Write JSON to file in addition to stdout')
    args = ap.parse_args()

    if not MAPPING_FILE.exists():
        sys.exit(f'Mapping file not found: {MAPPING_FILE}')
    mapping: dict[str, int] = json.loads(MAPPING_FILE.read_text(encoding='utf-8'))

    rows: list[dict] = []
    for code in sorted(mapping, key=lambda c: (c[0], int(c[1:]))):
        try:
            wp = fetch_wp(mapping[code])
        except requests.HTTPError as e:
            sys.stderr.write(f'[{code}] WP#{mapping[code]} fetch failed: {e}\n')
            continue
        rows.append(extract(wp, code))

    if not args.all:
        rows = [r for r in rows if r['assignee'] == args.assignee]

    payload = json.dumps(rows, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        args.out.write_text(payload + '\n', encoding='utf-8')
        sys.stderr.write(f'\nSaved {len(rows)} rows to {args.out}\n')

    return 0


if __name__ == '__main__':
    sys.exit(main())
