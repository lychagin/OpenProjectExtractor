#!/usr/bin/env bash
# Daily Postgres backup: bugs DB + DataLens DB. Rotation: 7 daily + 4 weekly.
#
# Designed to be run by cron from the extractor user:
#   0 3 * * * /srv/extractor/scripts/backup.sh >> /srv/backups/backup.log 2>&1
#
# Env vars (override defaults for testing):
#   BACKUP_ROOT     where to write dumps (default: /srv/backups)
#   SKIP_DATALENS   if set to "1", skip the DataLens dump (for test envs without it)
#
# Reads POSTGRES_USER and POSTGRES_DB from /srv/extractor/.env (if present).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_ROOT="${BACKUP_ROOT:-/srv/backups}"
DAILY="$BACKUP_ROOT/daily"
WEEKLY="$BACKUP_ROOT/weekly"

mkdir -p "$DAILY" "$WEEKLY"

# Source .env if present (for POSTGRES_USER / POSTGRES_DB on real VM deploys).
# In test/local runs the variables already come from the docker compose env.
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_ROOT/.env"
    set +a
fi

DATE=$(date +%Y-%m-%d)
DOW=$(date +%u)   # 1=Mon ... 7=Sun

# Prefer prod compose stack if datalens override is present, else light stack.
if [ -f "$REPO_ROOT/docker-compose.datalens.yml" ] && [ -f "$REPO_ROOT/docker-compose.prod.yml" ]; then
    COMPOSE="docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml"
elif [ -f "$REPO_ROOT/docker-compose.datalens.yml" ]; then
    COMPOSE="docker compose -f docker-compose.yml -f docker-compose.datalens.yml"
else
    COMPOSE="docker compose -f docker-compose.yml"
fi

# Bugs DB dump.
$COMPOSE exec -T postgres \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DAILY/bugs-$DATE.sql.gz"
echo "wrote $DAILY/bugs-$DATE.sql.gz ($(du -h "$DAILY/bugs-$DATE.sql.gz" | cut -f1))"

# DataLens DB dump (skip if explicitly disabled or service not present).
if [ "${SKIP_DATALENS:-0}" != "1" ]; then
    if $COMPOSE ps datalens-postgres --format json 2>/dev/null | grep -q '"State":"running"'; then
        $COMPOSE exec -T datalens-postgres \
            pg_dumpall -U postgres | gzip > "$DAILY/datalens-$DATE.sql.gz"
        echo "wrote $DAILY/datalens-$DATE.sql.gz ($(du -h "$DAILY/datalens-$DATE.sql.gz" | cut -f1))"
    else
        echo "datalens-postgres not running, skipping its dump"
    fi
fi

# Promote Sunday's dailies to weekly.
if [ "$DOW" = "7" ]; then
    cp "$DAILY/bugs-$DATE.sql.gz" "$WEEKLY/bugs-$DATE.sql.gz"
    if [ -f "$DAILY/datalens-$DATE.sql.gz" ]; then
        cp "$DAILY/datalens-$DATE.sql.gz" "$WEEKLY/datalens-$DATE.sql.gz"
    fi
    echo "promoted today's dump to weekly/"
fi

# Rotation: keep 7 daily, 4 weekly.
find "$DAILY" -name '*.sql.gz' -mtime +7 -delete
find "$WEEKLY" -name '*.sql.gz' -mtime +28 -delete

echo "backup OK at $(date -Iseconds)"
