import os
import sys
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Configuration
OPENPROJECT_URL = os.getenv('OPENPROJECT_URL', 'https://projects-customdev.wone-it.ru')
PROJECT_IDENTIFIER = os.getenv('PROJECT_IDENTIFIER', 'dom-zhkkh')
API_TOKEN = os.getenv('OPENPROJECT_API_TOKEN') or os.getenv('OPENPROJECTTOKEN')
OUTPUT_DIR = Path('output')
OUTPUT_PREFIX = 'res'
OUTPUT_SUFFIX = '.csv'
CSV_SEPARATOR = ';'
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
    """Fetch work packages from OpenProject API with pagination support."""
    url = f"{OPENPROJECT_URL}/api/v3/projects/{PROJECT_IDENTIFIER}/work_packages"

    filters = json.dumps([{"type": {"operator": "=", "values": [type_id]}}])
    params = {
        'offset': page,
        'pageSize': per_page,
        'filters': filters,
    }

    headers = get_api_headers()
    auth = get_api_auth()

    try:
        response = requests.get(url, headers=headers, params=params, auth=auth, timeout=30)
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


def extract_bugs():
    """Extract all bugs from the OpenProject project."""
    all_bugs = []
    page = 1
    total_count = None
    
    logger.info(f"Starting extraction from project: {PROJECT_IDENTIFIER}")
    logger.info(f"API URL: {OPENPROJECT_URL}/api/v3/projects/{PROJECT_IDENTIFIER}/work_packages")

    type_id = get_bug_type_id()
    logger.info(f"Resolved type '{BUG_TYPE_NAME}' to id={type_id}")

    while True:
        logger.info(f"Fetching page {page}...")
        data = fetch_work_packages(page=page, type_id=type_id)

        if total_count is None:
            total_count = data.get('total', 0)
            logger.info(f"Total work packages to process: {total_count}")

        elements = data.get('_embedded', {}).get('elements', [])
        if not elements:
            break

        for wp in elements:
            all_bugs.append({'id': wp.get('id'), 'title': wp.get('subject', '')})

        logger.info(f"Extracted {len(all_bugs)} / {total_count} bugs so far...")

        if len(all_bugs) >= total_count:
            break
        page += 1
    
    logger.info(f"Extraction complete. Total bugs found: {len(all_bugs)}")
    return all_bugs


def save_to_csv(bugs, output_dir=OUTPUT_DIR):
    """Save bugs to CSV file with datetime in filename."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = f"{OUTPUT_PREFIX}-{timestamp}{OUTPUT_SUFFIX}"
    filepath = output_dir / filename
    
    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile, delimiter=CSV_SEPARATOR, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(['id', 'title'])
        for bug in bugs:
            writer.writerow([bug['id'], bug['title']])
    
    logger.info(f"Results saved to: {filepath}")
    return filepath


def main():
    """Main entry point."""
    logger.info("OpenProject Bug Extractor started")
    
    try:
        bugs = extract_bugs()
        
        if not bugs:
            logger.warning("No bugs found in the project")
            return 1
        
        filepath = save_to_csv(bugs)
        logger.info(f"Successfully extracted {len(bugs)} bugs to {filepath}")
        return 0
    
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
