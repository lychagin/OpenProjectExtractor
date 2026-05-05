#!/usr/bin/env bash
# Integration test for scripts/backup.sh.
# Runs against the local docker compose stack (postgres must be up).
# Uses a temp BACKUP_ROOT to avoid touching the real /srv/backups path.
#
# Run: bash tests/test_backup.sh
# Exit codes: 0 on pass, non-zero on fail.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Pre-flight: postgres must be running.
if ! docker compose ps postgres --format json 2>/dev/null | grep -q '"State":"running"'; then
    echo "FAIL: postgres container is not running. Start with 'make up' first." >&2
    exit 1
fi

# Set up isolated backup root.
TEST_BACKUP_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_BACKUP_ROOT"' EXIT

# Run the backup script with overridden BACKUP_ROOT and a flag to skip DataLens
# (test stack doesn't include datalens-postgres).
BACKUP_ROOT="$TEST_BACKUP_ROOT" SKIP_DATALENS=1 bash scripts/backup.sh

# Assertion 1: a daily bugs dump exists.
DAILY_FILES=$(find "$TEST_BACKUP_ROOT/daily" -name 'bugs-*.sql.gz' 2>/dev/null | wc -l)
if [ "$DAILY_FILES" != "1" ]; then
    echo "FAIL: expected 1 bugs daily dump, found $DAILY_FILES" >&2
    ls -la "$TEST_BACKUP_ROOT/daily" >&2 || true
    exit 1
fi

# Assertion 2: the gzip is valid.
DUMP=$(find "$TEST_BACKUP_ROOT/daily" -name 'bugs-*.sql.gz' | head -1)
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "FAIL: backup file is not valid gzip: $DUMP" >&2
    exit 1
fi

# Assertion 3: the decompressed dump contains the expected schema markers.
# Note: use { gunzip ... || true; } to suppress SIGPIPE (141) that grep -q causes
# when it exits early after finding a match; pipefail would otherwise treat it as failure.
if ! { gunzip -c "$DUMP" || true; } | grep -q 'CREATE TABLE.*bugs\|COPY.*bugs'; then
    echo "FAIL: decompressed dump does not look like a bugs schema dump" >&2
    { gunzip -c "$DUMP" || true; } | head -20 >&2
    exit 1
fi

echo "PASS: backup.sh produced a valid bugs dump in $TEST_BACKUP_ROOT/daily/"
