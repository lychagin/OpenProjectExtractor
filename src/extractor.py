import os
import sys
import csv
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
API_TOKEN = os.getenv('OPENPROJECT_API_TOKEN') or os.getenv('OPENPROJECTTOKEN') or os.getenv('OPENPROJECTTOKEN')
OUTPUT_DIR = Path('output')
OUTPUT_PREFIX = 'res'
OUTPUT_SUFFIX = '.csv'
CSV_SEPARATOR = ';'
DEFAULT_PAGE_SIZE = 100


def get_api_headers():
    """Create headers for API requests."""
    if not API_TOKEN:
        logger.error("OPENPROJECT_API_TOKEN not found in environment variables")
        raise ValueError("OPENPROJECT_API_TOKEN is required")
    return {
        'Authorization': f'Bearer {API_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }


def fetch_work_packages(page=1, per_page=DEFAULT_PAGE_SIZE):
    """Fetch work packages from OpenProject API with pagination support."""
    url = f"{OPENPROJECT_URL}/api/v3/projects/{PROJECT_IDENTIFIER}/work_packages"
    
    params = {
        'page': page,
        'perPage': per_page,
        'filters': '[{"type":{"operator":"=","values":["Bug"]}}]'
    }
    
    headers = get_api_headers()
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
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
    
    while True:
        logger.info(f"Fetching page {page}...")
        data = fetch_work_packages(page=page)
        
        # Get total count from first page
        if total_count is None:
            total_count = data.get('_meta', {}).get('count', 0)
            logger.info(f"Total work packages to process: {total_count}")
        
        # Extract work packages from _embedded or _links
        elements = data.get('_embedded', {}).get('elements', [])
        
        if not elements:
            logger.info("No more work packages to fetch.")
            break
        
        for wp in elements:
            bug_id = wp.get('id')
            bug_title = wp.get('subject', '')
            all_bugs.append({
                'id': bug_id,
                'title': bug_title
            })
        
        logger.info(f"Extracted {len(all_bugs)} bugs so far...")
        
        # Check if there are more pages
        next_link = data.get('_links', {}).get('next', {})
        if not next_link:
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
