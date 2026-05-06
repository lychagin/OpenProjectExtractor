"""Assign created product-sort subtasks to Sergey/Vadim per agreed split.

Reads the mapping file produced by create_product_sort_subtasks.py and
PATCHes each work package with the right assignee.

Vadim Valiev (id 75)  — CSV import/export bundle + e2e: B2, B6, B7, F7, F10, B10
Сергей Лычагин (id 46) — everything else

B10 was reassigned from Sergey to Vadim after initial split (see WP#6865
activity for the rationale).
"""
from __future__ import annotations

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

SERGEY_ID = 46
VADIM_ID = 75

VADIM_TASKS = {'B2', 'B6', 'B7', 'F7', 'F10', 'B10'}


def auth():
    if not TOKEN:
        sys.exit('OPENPROJECTTOKEN is required (set in .env)')
    return ('apikey', TOKEN)


def headers():
    return {'Accept': 'application/json', 'Content-Type': 'application/json'}


def get_wp(wp_id: int) -> dict:
    r = requests.get(f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}',
                     auth=auth(), headers=headers(),
                     verify=str(CERT_PATH), timeout=30)
    r.raise_for_status()
    return r.json()


def assign(wp_id: int, user_id: int, lock_version: int) -> dict:
    payload = {
        'lockVersion': lock_version,
        '_links': {'assignee': {'href': f'/api/v3/users/{user_id}'}},
    }
    r = requests.patch(f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}',
                       auth=auth(), headers=headers(),
                       json=payload, verify=str(CERT_PATH), timeout=30)
    if r.status_code >= 400:
        sys.stderr.write(f'PATCH WP#{wp_id} failed [{r.status_code}]: {r.text}\n')
        r.raise_for_status()
    return r.json()


def main() -> int:
    if not MAPPING_FILE.exists():
        sys.exit(f'Mapping file not found: {MAPPING_FILE}')
    mapping: dict[str, int] = json.loads(MAPPING_FILE.read_text(encoding='utf-8'))

    for code in sorted(mapping):
        wp_id = mapping[code]
        user_id = VADIM_ID if code in VADIM_TASKS else SERGEY_ID
        user_label = 'Vadim Valiev' if user_id == VADIM_ID else 'Сергей Лычагин'

        wp = get_wp(wp_id)
        current_assignee = wp.get('_links', {}).get('assignee', {}).get('title')
        if current_assignee == user_label:
            print(f'[{code}] WP#{wp_id} already assigned to {user_label}, skip')
            continue

        updated = assign(wp_id, user_id, wp['lockVersion'])
        new_assignee = updated.get('_links', {}).get('assignee', {}).get('title')
        print(f'[{code}] WP#{wp_id} → {new_assignee}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
