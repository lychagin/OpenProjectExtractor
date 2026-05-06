"""Thin OpenProject API client shared by helper scripts.

Provides:
  * ``client()`` — returns ``(auth_tuple, base_url, cert_path)``
  * ``get_wp(wp_id)`` — fetch a work package
  * ``patch_wp(wp_id, payload)`` — PATCH /api/v3/work_packages/{id}
  * ``post_comment(wp_id, raw_markdown)`` — POST a comment to activities
  * ``find_status_id(name)`` — lookup status id by case-insensitive name
  * ``find_user_id(query)`` — lookup user id by name/email substring (best match)

Loads credentials from ``$OPENPROJECTTOKEN`` (and optional ``$OPENPROJECT_URL``)
via ``.env``. CA bundle expected at ``../.cert/bundle.pem``.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

OPENPROJECT_URL = os.getenv('OPENPROJECT_URL', 'https://projects-customdev.wone-it.ru')
TOKEN = os.getenv('OPENPROJECTTOKEN')
CERT_PATH = Path(__file__).resolve().parent.parent / '.cert' / 'bundle.pem'

DEFAULT_TIMEOUT = 30


def _auth() -> tuple[str, str]:
    if not TOKEN:
        sys.exit('OPENPROJECTTOKEN is required (set in .env)')
    return ('apikey', TOKEN)


def _headers(json_body: bool = False) -> dict[str, str]:
    h = {'Accept': 'application/json'}
    if json_body:
        h['Content-Type'] = 'application/json'
    return h


def get_wp(wp_id: int) -> dict[str, Any]:
    r = requests.get(
        f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}',
        auth=_auth(), headers=_headers(),
        verify=str(CERT_PATH), timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def patch_wp(wp_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    r = requests.patch(
        f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}',
        auth=_auth(), headers=_headers(json_body=True),
        json=payload, verify=str(CERT_PATH), timeout=DEFAULT_TIMEOUT,
    )
    if r.status_code >= 400:
        sys.stderr.write(f'PATCH WP#{wp_id} failed [{r.status_code}]: {r.text}\n')
        r.raise_for_status()
    return r.json()


def post_comment(wp_id: int, raw_markdown: str) -> dict[str, Any]:
    """Post a comment to the WP activity tab."""
    payload = {'comment': {'raw': raw_markdown}}
    r = requests.post(
        f'{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}/activities',
        auth=_auth(), headers=_headers(json_body=True),
        json=payload, verify=str(CERT_PATH), timeout=DEFAULT_TIMEOUT,
    )
    if r.status_code >= 400:
        sys.stderr.write(f'POST comment to WP#{wp_id} failed [{r.status_code}]: {r.text}\n')
        r.raise_for_status()
    return r.json()


@lru_cache(maxsize=1)
def _statuses() -> list[dict[str, Any]]:
    r = requests.get(
        f'{OPENPROJECT_URL}/api/v3/statuses',
        auth=_auth(), headers=_headers(),
        verify=str(CERT_PATH), timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get('_embedded', {}).get('elements', [])


def find_status_id(name: str) -> int:
    target = name.strip().lower()
    for s in _statuses():
        if s.get('name', '').lower() == target:
            return int(s['id'])
    available = ', '.join(s.get('name', '') for s in _statuses())
    sys.exit(f'Status not found: {name!r}. Available: {available}')


@lru_cache(maxsize=1)
def _users() -> list[dict[str, Any]]:
    r = requests.get(
        f'{OPENPROJECT_URL}/api/v3/users',
        auth=_auth(), headers=_headers(),
        params={'pageSize': 200},
        verify=str(CERT_PATH), timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get('_embedded', {}).get('elements', [])


def find_user_id(query: str) -> int:
    """Match by exact name/login/email, then by substring (case-insensitive)."""
    q = query.strip().lower()
    users = _users()
    for u in users:
        for field in ('name', 'login', 'email'):
            if (u.get(field) or '').lower() == q:
                return int(u['id'])
    matches = [
        u for u in users
        if any(q in (u.get(f) or '').lower() for f in ('name', 'login', 'email'))
    ]
    if len(matches) == 1:
        return int(matches[0]['id'])
    if not matches:
        sys.exit(f'User not found: {query!r}')
    names = ', '.join(f'{u.get("name")} (id={u["id"]})' for u in matches)
    sys.exit(f'User query {query!r} matched multiple: {names}. Refine.')


def update_status(wp_id: int, status_name: str) -> dict[str, Any]:
    """High-level: set WP status to ``status_name`` (e.g. 'In progress', 'Review')."""
    wp = get_wp(wp_id)
    status_id = find_status_id(status_name)
    return patch_wp(wp_id, {
        'lockVersion': wp['lockVersion'],
        '_links': {'status': {'href': f'/api/v3/statuses/{status_id}'}},
    })


def update_assignee(wp_id: int, user_query: str) -> dict[str, Any]:
    """High-level: reassign WP to user matched by ``user_query``."""
    wp = get_wp(wp_id)
    user_id = find_user_id(user_query)
    return patch_wp(wp_id, {
        'lockVersion': wp['lockVersion'],
        '_links': {'assignee': {'href': f'/api/v3/users/{user_id}'}},
    })
