"""OpenProject HTTP API client.

Pure data fetching — no DB, no CSV. Returns raw work-package dicts so callers
can decide what to do with them (DB upsert, JSON dump, etc.).
"""
import json
import logging
import os
from typing import Iterator

import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

load_dotenv()

OPENPROJECT_URL = os.getenv('OPENPROJECT_URL', 'https://projects-customdev.wone-it.ru')
PROJECT_IDENTIFIER = os.getenv('PROJECT_IDENTIFIER', 'dom-zhkkh')
API_TOKEN = os.getenv('OPENPROJECT_API_TOKEN') or os.getenv('OPENPROJECTTOKEN')
DEFAULT_PAGE_SIZE = 100
BUG_TYPE_NAME = os.getenv('BUG_TYPE_NAME', 'Bug')


def get_api_auth():
    """Return Basic auth tuple for OpenProject API ('apikey' user, token as password)."""
    if not API_TOKEN:
        logger.error("OPENPROJECT_API_TOKEN not found in environment variables")
        raise ValueError("OPENPROJECT_API_TOKEN is required")
    return ('apikey', API_TOKEN)


def get_api_headers():
    return {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def get_bug_type_id():
    """Look up the numeric type ID for BUG_TYPE_NAME in this project."""
    url = f"{OPENPROJECT_URL}/api/v3/projects/{PROJECT_IDENTIFIER}/types"
    response = requests.get(url, headers=get_api_headers(), auth=get_api_auth(), timeout=30)
    response.raise_for_status()
    for el in response.json().get('_embedded', {}).get('elements', []):
        if el.get('name') == BUG_TYPE_NAME:
            return str(el.get('id'))
    raise ValueError(f"Type '{BUG_TYPE_NAME}' not found in project '{PROJECT_IDENTIFIER}'")


def fetch_work_packages(page=1, per_page=DEFAULT_PAGE_SIZE, type_id=None):
    """Fetch one page of work packages from OpenProject API."""
    url = f"{OPENPROJECT_URL}/api/v3/projects/{PROJECT_IDENTIFIER}/work_packages"
    filters = json.dumps([{"type": {"operator": "=", "values": [type_id]}}])
    params = {
        'offset': page,
        'pageSize': per_page,
        'filters': filters,
    }
    try:
        response = requests.get(
            url, headers=get_api_headers(), params=params, auth=get_api_auth(), timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            logger.error("Authentication failed. Check your API token.")
        elif response.status_code == 404:
            logger.error(f"Project '{PROJECT_IDENTIFIER}' not found.")
        else:
            logger.error(f"HTTP error occurred: {e}")
        raise
    except requests.exceptions.ConnectionError:
        logger.error(f"Failed to connect to {OPENPROJECT_URL}")
        raise
    except requests.exceptions.Timeout:
        logger.error(f"Request to {OPENPROJECT_URL} timed out")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        raise


def iter_bugs() -> Iterator[dict]:
    """Yield every bug work-package in the project, paginating through the API."""
    type_id = get_bug_type_id()
    logger.info(f"Resolved type '{BUG_TYPE_NAME}' to id={type_id}")

    page = 1
    seen = 0
    total = None
    while True:
        data = fetch_work_packages(page=page, type_id=type_id)
        if total is None:
            total = data.get('total', 0)
            logger.info(f"Total work packages to process: {total}")

        elements = data.get('_embedded', {}).get('elements', [])
        if not elements:
            break

        for wp in elements:
            yield wp
            seen += 1

        logger.info(f"Fetched {seen} / {total} bugs so far...")
        if seen >= total:
            break
        page += 1


def extract_bugs() -> list[dict]:
    """Materialize iter_bugs() into a list. Convenience for callers that want everything in memory."""
    return list(iter_bugs())
