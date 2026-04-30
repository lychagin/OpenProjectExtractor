"""Print a single work package as JSON, for quick debugging.

Usage: python -m src.show_bug <id>
"""
import json
import sys

import requests

from src.client import OPENPROJECT_URL, get_api_auth, get_api_headers


def main():
    if len(sys.argv) != 2:
        print("usage: python -m src.show_bug <id>", file=sys.stderr)
        return 2
    wp_id = sys.argv[1]
    r = requests.get(
        f"{OPENPROJECT_URL}/api/v3/work_packages/{wp_id}",
        headers=get_api_headers(),
        auth=get_api_auth(),
        timeout=30,
    )
    r.raise_for_status()
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
