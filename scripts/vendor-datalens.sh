#!/bin/bash
# Re-vendor docker-compose.datalens.yml from the upstream datalens-tech/datalens repo.
#
# Run from the repo root:  ./scripts/vendor-datalens.sh
#
# Fetches the current upstream docker-compose.yaml, applies our local changes
# (rename `postgres:` → `datalens-postgres:`, inline POSTGRES_* defaults), and
# overwrites docker-compose.datalens.yml. The header comment in that file
# documents what changes are being applied — keep this script in sync.
#
# Diff before committing! Upstream may have introduced new env vars or services
# we need to handle.

set -euo pipefail

cd "$(dirname "$0")/.."

UPSTREAM_URL="https://raw.githubusercontent.com/datalens-tech/datalens/main/docker-compose.yaml"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

echo "Fetching $UPSTREAM_URL ..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$UPSTREAM_URL" -o "$TMP"
else
    wget -qO "$TMP" "$UPSTREAM_URL"
fi

# Preserve our header comment from the existing file (everything before `services:`).
HEADER=$(awk '/^services:$/{exit} {print}' docker-compose.datalens.yml 2>/dev/null || echo "")

{
    if [[ -n "$HEADER" ]]; then
        echo "$HEADER"
    fi
    sed \
        -e 's/^  postgres:$/  datalens-postgres:/' \
        -e 's/^      postgres:$/      datalens-postgres:/' \
        -e 's|${POSTGRES_HOST:-postgres}|datalens-postgres|g' \
        -e 's|${POSTGRES_HOST:-localhost}|localhost|g' \
        -e 's|${POSTGRES_PORT:-5432}|5432|g' \
        -e 's|${POSTGRES_USER:-pg-user}|pg-user|g' \
        -e 's|${POSTGRES_PASSWORD:-postgres}|postgres|g' \
        -e 's|${POSTGRES_DB_COMPENG:-pg-compeng-db}|pg-compeng-db|g' \
        -e 's|${POSTGRES_DB_AUTH:-pg-auth-db}|pg-auth-db|g' \
        -e 's|${POSTGRES_DB_US:-pg-us-db}|pg-us-db|g' \
        -e 's|${POSTGRES_DB_DEMO:-pg-demo-db}|pg-demo-db|g' \
        -e 's|${POSTGRES_DB_META_MANAGER:-pg-meta-manager-db}|pg-meta-manager-db|g' \
        -e 's|${POSTGRES_DB_TEMPORAL:-pg-temporal-db}|pg-temporal-db|g' \
        -e 's|${POSTGRES_DB_TEMPORAL_VISIBILITY:-pg-temporal-visibility-db}|pg-temporal-visibility-db|g' \
        -e 's|${POSTGRES_ARGS:-}||g' \
        "$TMP"
} > docker-compose.datalens.yml.new

mv docker-compose.datalens.yml.new docker-compose.datalens.yml
echo "Wrote docker-compose.datalens.yml ($(wc -l < docker-compose.datalens.yml) lines)"
echo "Review the diff (git diff docker-compose.datalens.yml) before committing."
