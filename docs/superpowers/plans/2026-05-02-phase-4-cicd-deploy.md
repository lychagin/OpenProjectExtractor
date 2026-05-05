# Phase 4 — CI/CD + production deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a complete production-deploy machinery to the repo: GH Actions workflow that publishes the extractor image to `ghcr.io` (private), a `docker-compose.prod.yml` override that pulls the published image, an nginx reverse-proxy with TLS in front of DataLens, a backup script with an automated test, an idempotent VM provisioning script, and an Operations runbook in README. Result: `git push main` → image lands in ghcr.io within ~2 minutes; once a VM is provisioned, cron-pull picks up new images within 5 minutes.

**Architecture:** All deploy machinery is config + shell. No Python changes. Local dev workflow (`make up` / `make datalens-up`) stays unchanged — production layers in via a third compose override file. Substitution of `GHCR_OWNER` and `SERVER_NAME` happens through Docker Compose env-var interpolation (`${VAR}` from `.env`) and nginx's official `envsubst`-on-templates entrypoint (no manual sed at provision time).

**Tech Stack:** GitHub Actions, GitHub Container Registry (ghcr.io), Docker Compose v2 (multi-file overlays), nginx:alpine (with template-based config rendering), Let's Encrypt / certbot, bash, ufw, cron.

**Spec:** `docs/superpowers/specs/2026-05-02-phase-4-cicd-deploy-design.md`

---

## File structure

| File | Action | Purpose |
|---|---|---|
| `.github/workflows/build-image.yml` | Create | Builds and pushes the extractor image to `ghcr.io` on every `main` push (or manual dispatch). |
| `.env.example` | Modify | Add `GHCR_OWNER` and `SERVER_NAME` so prod deployers know what to fill in. |
| `docker-compose.prod.yml` | Create | Production override: replaces `build: .` with `image: ghcr.io/...:latest` and adds the `nginx` service. |
| `nginx/templates/datalens.conf.template` | Create | nginx reverse-proxy config with `${SERVER_NAME}` placeholder; rendered by nginx's entrypoint at container start. |
| `scripts/backup.sh` | Create | Daily Postgres dump script (bugs DB + DataLens DB) with 7-daily/4-weekly rotation. |
| `scripts/provision-vm.sh` | Create | Idempotent one-time setup script for a fresh Ubuntu 24.04 LTS VM. |
| `tests/test_backup.sh` | Create | Shell-script integration test: runs `backup.sh` against the local stack, asserts a valid `.sql.gz` file appears. |
| `Makefile` | Modify | Add `prod-up`, `prod-down`, `prod-logs`, `nginx-check` targets. |
| `README.md` | Modify | Add `## Production deploy` and `## Operations runbook` sections. |

No source code in `src/` changes. No new Python tests. The existing `tests/test_history_views.py` and `tests/test_db.py` remain unchanged.

---

## Task 1: GitHub Actions workflow + ghcr.io publication

**Files:**
- Create: `.github/workflows/build-image.yml`
- Modify: `.env.example` (add `GHCR_OWNER`)

- [ ] **Step 1: Create `.github/workflows/build-image.yml`**

Full file contents:

```yaml
name: Build and push image

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Lowercase owner for registry path
        run: echo "REPO_LOWER=$(echo ${{ github.repository_owner }} | tr '[:upper:]' '[:lower:]')" >> $GITHUB_ENV

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ env.REPO_LOWER }}/openproject-extractor:latest
            ghcr.io/${{ env.REPO_LOWER }}/openproject-extractor:${{ github.sha }}
```

- [ ] **Step 2: Add `GHCR_OWNER` to `.env.example`**

Append to the end of `.env.example`:

```bash

# GitHub Container Registry owner (lowercase) — used by docker-compose.prod.yml to
# pull the published extractor image. For repo github.com/lychagin/OpenProjectExtractor
# set this to "lychagin".
GHCR_OWNER=

# Public hostname under which DataLens is served. Used by nginx envsubst at
# container start. Example: bugs.example.com or whatever your VM provider gave you.
SERVER_NAME=
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build-image.yml .env.example
git commit -m "feat(ci): GH Actions workflow to publish extractor image to ghcr.io"
```

- [ ] **Step 4: Push and verify the workflow runs**

```bash
git push
```

Wait ~15 seconds, then:

```bash
gh run list --workflow=build-image.yml --limit 1
```

Expected: a run in `in_progress` or `completed` state. If `completed` and `success`, proceed. If `failure`, check `gh run view <run-id> --log` and fix.

- [ ] **Step 5: Confirm the image landed in ghcr.io**

Open <https://github.com/lychagin?tab=packages> (or `https://github.com/<owner>?tab=packages`). Expected: a package named `openproject-extractor` listed with two tags — `latest` and the commit SHA. Click into it to confirm visibility is `Private` (matches the repo).

If the package appears as **Public** by mistake (GitHub default for some accounts): in the package settings, change visibility to Private.

- [ ] **Step 6: No further commit (verification only)**

---

## Task 2: docker-compose.prod.yml + Makefile prod targets

**Files:**
- Create: `docker-compose.prod.yml`
- Modify: `Makefile`

- [ ] **Step 1: Create `docker-compose.prod.yml`** (extractor override only — nginx added in Task 3)

Full file contents:

```yaml
# Production override. Layered on top of docker-compose.yml +
# docker-compose.datalens.yml. Replaces the dev `build: .` with the image
# published to ghcr.io by .github/workflows/build-image.yml.
#
# Bring up:
#   docker compose -f docker-compose.yml \
#                  -f docker-compose.datalens.yml \
#                  -f docker-compose.prod.yml \
#                  up -d
#
# (Or `make prod-up`.)

services:
  extractor:
    build: !reset null
    image: ghcr.io/${GHCR_OWNER}/openproject-extractor:latest
    pull_policy: always
```

- [ ] **Step 2: Add Makefile targets**

Open `Makefile` and add these targets after the existing DataLens block (after the `datalens-logs` target):

```makefile
# --- Production stack (extractor pulled from ghcr.io + DataLens + nginx) ----
COMPOSE_PROD := -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml

prod-up:
	docker compose $(COMPOSE_PROD) up -d

prod-down:
	docker compose $(COMPOSE_PROD) down

prod-logs:
	docker compose $(COMPOSE_PROD) logs -f extractor nginx
```

Also add `prod-up prod-down prod-logs` to the `.PHONY:` line at the top of the Makefile.

- [ ] **Step 3: Verify the override parses correctly**

With a temporary `GHCR_OWNER` set, run:

```bash
GHCR_OWNER=lychagin docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -A2 'extractor:' | head -20
```

Expected output: the `extractor` section shows `image: ghcr.io/lychagin/openproject-extractor:latest` and **no** `build:` field. (`!reset null` removed it.)

If the output still shows `build: .`, the override didn't apply — investigate compose file order.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.prod.yml Makefile
git commit -m "feat(deploy): docker-compose.prod.yml + make prod-up/down/logs"
```

---

## Task 3: nginx reverse-proxy with TLS

**Files:**
- Create: `nginx/templates/datalens.conf.template`
- Modify: `docker-compose.prod.yml` (add `nginx` service)
- Modify: `Makefile` (add `nginx-check`)

- [ ] **Step 1: Create the nginx template**

Create directory `nginx/templates/` and file `nginx/templates/datalens.conf.template` with these contents:

```nginx
# Rendered by nginx:alpine entrypoint at container start: the official image
# runs envsubst on /etc/nginx/templates/*.template and writes the result to
# /etc/nginx/conf.d/. Only ${SERVER_NAME} is substituted (passed in as env).
# All other $variables are nginx runtime variables and are preserved (we use
# the entrypoint's default ENVSUBST_TEMPLATE_DIR but a default vars filter).

server {
    listen 443 ssl http2;
    server_name ${SERVER_NAME};

    ssl_certificate     /etc/letsencrypt/live/${SERVER_NAME}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${SERVER_NAME}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 50M;

    location / {
        proxy_pass http://ui:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}

server {
    listen 80;
    server_name ${SERVER_NAME};
    return 301 https://$host$request_uri;
}
```

Note: nginx variables like `$host`, `$remote_addr`, `$proxy_add_x_forwarded_for`, `$scheme`, `$http_upgrade`, `$request_uri` are NOT in the `${VAR}` form and are preserved by `envsubst` (which only replaces variables it's explicitly told about — in the official nginx image's defaults, that's only `${VAR}`-style references that are also defined in the env).

- [ ] **Step 2: Add `nginx` service to `docker-compose.prod.yml`**

Append to `docker-compose.prod.yml` (after the `extractor` service block):

```yaml
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      SERVER_NAME: ${SERVER_NAME}
    volumes:
      - ./nginx/templates:/etc/nginx/templates:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
    depends_on:
      - ui
```

- [ ] **Step 3: Add `nginx-check` Makefile target**

Append to `Makefile` (after the prod targets):

```makefile
# Smoke-test the nginx template + envsubst rendering with a self-signed cert,
# without needing a real domain or Let's Encrypt. Useful before deploying.
nginx-check:
	@TMPDIR=$$(mktemp -d) && \
	mkdir -p $$TMPDIR/live/localhost && \
	openssl req -x509 -nodes -newkey rsa:2048 \
	    -keyout $$TMPDIR/live/localhost/privkey.pem \
	    -out    $$TMPDIR/live/localhost/fullchain.pem \
	    -subj "/CN=localhost" -days 1 2>/dev/null && \
	docker run --rm \
	    -e SERVER_NAME=localhost \
	    -v $$PWD/nginx/templates:/etc/nginx/templates:ro \
	    -v $$TMPDIR:/etc/letsencrypt:ro \
	    nginx:alpine nginx -t && \
	rm -rf $$TMPDIR && \
	echo "nginx config OK"
```

Also add `nginx-check` to the `.PHONY:` line.

- [ ] **Step 4: Run `make nginx-check`**

```bash
make nginx-check
```

Expected output ends with:

```
nginx: configuration file /etc/nginx/nginx.conf test is successful
nginx config OK
```

If you see "nginx: [emerg] cannot load certificate" — `openssl` failed to write the cert; check that openssl is installed (`apt-get install -y openssl` if needed).

If you see "nginx: [emerg] unknown directive" — there's a syntax error in the template; review.

- [ ] **Step 5: Commit**

```bash
git add nginx/templates/datalens.conf.template docker-compose.prod.yml Makefile
git commit -m "feat(deploy): nginx reverse-proxy + TLS + make nginx-check"
```

---

## Task 4: backup.sh + automated test

**Files:**
- Create: `scripts/backup.sh`
- Create: `tests/test_backup.sh`

- [ ] **Step 1: Write the failing test first**

Create `tests/test_backup.sh`:

```bash
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
if ! gunzip -c "$DUMP" | grep -q 'CREATE TABLE.*bugs\|COPY.*bugs'; then
    echo "FAIL: decompressed dump does not look like a bugs schema dump" >&2
    gunzip -c "$DUMP" | head -20 >&2
    exit 1
fi

echo "PASS: backup.sh produced a valid bugs dump in $TEST_BACKUP_ROOT/daily/"
```

Make it executable:

```bash
chmod +x tests/test_backup.sh
```

- [ ] **Step 2: Run the test, expect failure**

```bash
bash tests/test_backup.sh
```

Expected: error like `bash: scripts/backup.sh: No such file or directory` or similar — the script doesn't exist yet. Good, that's the failing-test state.

- [ ] **Step 3: Write `scripts/backup.sh`**

Create `scripts/backup.sh`:

```bash
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
```

Make it executable:

```bash
chmod +x scripts/backup.sh
```

- [ ] **Step 4: Run the test, expect pass**

```bash
bash tests/test_backup.sh
```

Expected: `PASS: backup.sh produced a valid bugs dump in /tmp/tmp.XXXXXX/daily/`.

If the test reports `FAIL: postgres container is not running`, run `make up` first then retry.

- [ ] **Step 5: Commit**

```bash
git add scripts/backup.sh tests/test_backup.sh
git commit -m "feat(deploy): backup.sh + integration test"
```

---

## Task 5: VM provisioning script

This script runs ONCE on a fresh Ubuntu 24.04 LTS VM. It cannot be unit-tested (mutates a real system); the verification is "run on a throwaway VM, then re-run; second run produces no errors."

**Files:**
- Create: `scripts/provision-vm.sh`

- [ ] **Step 1: Create `scripts/provision-vm.sh`**

```bash
#!/usr/bin/env bash
# Idempotent one-time provisioning for a fresh Ubuntu 24.04 LTS VM.
# Run as root: sudo bash scripts/provision-vm.sh
#
# Sets up: docker, docker compose, ufw, certbot, the `extractor` user, the
# /srv/extractor and /srv/backups directories, and clones the repo.
#
# Does NOT do (manual handoff steps printed at the end):
#   - copy .env and .cert/bundle.pem from your laptop
#   - docker login ghcr.io with a personal access token
#   - certbot certonly to obtain the TLS certificate
#   - first `make prod-up`
#   - change DataLens admin password via UI

set -euo pipefail

if [ "$(id -u)" != "0" ]; then
    echo "Run as root (sudo bash $0)." >&2
    exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/lychagin/OpenProjectExtractor.git}"
EXTRACTOR_HOME=/srv/extractor
BACKUP_DIR=/srv/backups

echo "==> System updates"
apt-get update -y
apt-get upgrade -y

echo "==> Base packages"
apt-get install -y \
    curl ca-certificates gnupg lsb-release \
    ufw fail2ban certbot openssl

echo "==> Docker engine + compose plugin"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
else
    echo "    docker already installed: $(docker --version)"
fi

echo "==> extractor user"
if ! id -u extractor >/dev/null 2>&1; then
    useradd -m -s /bin/bash extractor
fi
usermod -aG docker extractor

echo "==> directories"
mkdir -p "$EXTRACTOR_HOME" "$BACKUP_DIR"
chown -R extractor:extractor "$EXTRACTOR_HOME" "$BACKUP_DIR"
chmod 750 "$EXTRACTOR_HOME" "$BACKUP_DIR"

echo "==> firewall (ufw)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP redirect + Lets Encrypt challenge'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

echo "==> repo clone"
if [ ! -d "$EXTRACTOR_HOME/.git" ]; then
    sudo -u extractor -H git clone "$REPO_URL" "$EXTRACTOR_HOME"
else
    echo "    repo already cloned, fetching latest"
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" fetch --all --prune
    sudo -u extractor -H git -C "$EXTRACTOR_HOME" pull --ff-only
fi

echo "==> cron entries (if not present)"
CRON_FILE="/etc/cron.d/openproject-extractor"
if [ ! -f "$CRON_FILE" ]; then
    cat > "$CRON_FILE" <<'EOF'
# OpenProjectExtractor production cron (managed by provision-vm.sh).
# Runs as the extractor user.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Auto-pull the latest extractor image every 5 minutes; restarts only if image changed.
*/5 * * * * extractor cd /srv/extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml pull --quiet extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml up -d extractor >> /srv/extractor/cron.log 2>&1

# Daily Postgres backups at 03:00.
0 3 * * * extractor /srv/extractor/scripts/backup.sh >> /srv/backups/backup.log 2>&1
EOF
    chmod 644 "$CRON_FILE"
    echo "    wrote $CRON_FILE"
else
    echo "    $CRON_FILE already present, leaving as-is"
fi

cat <<'EOF'

============================================================
PROVISIONING DONE. Next steps (manual):
============================================================

1. Copy secrets to the VM (from your laptop):

   scp .env       extractor@<VM-IP>:/srv/extractor/.env
   scp -r .cert   extractor@<VM-IP>:/srv/extractor/

   On the VM:
   sudo -u extractor chmod 600 /srv/extractor/.env /srv/extractor/.cert/bundle.pem

2. Edit /srv/extractor/.env on the VM, ensure:
   - GHCR_OWNER=<your github username, lowercase>
   - SERVER_NAME=<your.subdomain.example.com>
   - All other secrets carried over from your local .env.

3. Authenticate Docker to GitHub Container Registry:

   sudo -u extractor -i
   # Generate a GitHub PAT with read:packages scope at
   # https://github.com/settings/tokens (classic, no expiry preferred)
   echo "<YOUR_GH_PAT>" | docker login ghcr.io -u <your_github_username> --password-stdin

4. Obtain TLS certificate (port 80 must be free at this point — nothing on the VM listens
   there yet):

   certbot certonly --standalone \
       -d <your.subdomain.example.com> \
       --agree-tos -m <your-email>

5. Bring up the production stack (as extractor user):

   sudo -u extractor -i
   cd /srv/extractor
   make prod-up
   docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
       logs -f extractor nginx ui

6. Visit https://<your.subdomain.example.com>/ and log in as admin/admin.
   Immediately go to Settings -> Users and change the admin password.
   Update DATALENS_ADMIN_PASSWORD in /srv/extractor/.env if you keep it there.

7. Sanity-check the cron is running:

   tail -f /srv/extractor/cron.log     # should see "no new image" pulls every 5 min
   tail -f /srv/backups/backup.log     # populated after first 03:00 run
============================================================

EOF
```

Make it executable:

```bash
chmod +x scripts/provision-vm.sh
```

- [ ] **Step 2: Lint with `bash -n`**

```bash
bash -n scripts/provision-vm.sh
```

Expected: no output (syntactically valid). If you have shellcheck installed (`apt-get install -y shellcheck` or `brew install shellcheck`), run it too:

```bash
shellcheck scripts/provision-vm.sh || echo "shellcheck not installed, skipping"
```

Don't fix shellcheck warnings unless they're errors — minor style suggestions can be left.

- [ ] **Step 3: Commit**

```bash
git add scripts/provision-vm.sh
git commit -m "feat(deploy): scripts/provision-vm.sh for one-time VM setup"
```

---

## Task 6: README "Production deploy" + "Operations runbook" sections

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the new sections**

Insert these two sections at the very end of `README.md` (after the existing "## OpenProject API quirks worth remembering" section):

```markdown
## Production deploy

The repo ships everything needed to run this stack 24/7 on a single Ubuntu 24.04 LTS VM (8 GB RAM, 50 GB disk, 2 vCPU).

### One-time setup

On a fresh VM:

```bash
sudo bash scripts/provision-vm.sh
```

This installs Docker, sets up the `extractor` user, configures `ufw` (only 22/80/443 open), clones the repo into `/srv/extractor`, and writes the cron jobs. The script then prints a manual checklist for the steps it cannot do automatically:

1. **Copy secrets** (`scp .env extractor@vm:/srv/extractor/.env` and `scp -r .cert extractor@vm:/srv/extractor/`).
2. **Edit `/srv/extractor/.env`** on the VM and set `GHCR_OWNER`, `SERVER_NAME`, and the other secrets.
3. **`docker login ghcr.io`** with a GitHub Personal Access Token (`read:packages` scope).
4. **`certbot certonly --standalone`** to obtain the TLS certificate.
5. **`make prod-up`** to bring up the full stack.
6. **Change DataLens `admin/admin`** via the UI immediately on first login.

### How the deploy pipeline works

```
git push main → GH Actions builds image → ghcr.io (private) ← cron pulls every 5 min
```

`.github/workflows/build-image.yml` triggers on every push to `main` (or manual dispatch) and pushes two tags: `:latest` and `:<commit-sha>`. On the VM, a cron entry under `/etc/cron.d/openproject-extractor` runs every 5 minutes:

```bash
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    pull --quiet extractor && \
docker compose ... up -d extractor
```

`pull --quiet` only fetches the manifest (~1 KB) when the image hasn't changed. `up -d` only restarts the container when the local image SHA differs from what's running.

End-to-end push-to-prod latency: 1–2 minutes (GH Actions build) + up to 5 minutes (cron poll) = **≤7 minutes**.

## Operations runbook

### Inspect status

```bash
# On the VM as extractor user
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml ps
docker compose ... logs --tail=50 extractor      # last extractor cycles
docker compose ... logs --tail=50 nginx          # request log
tail -f /srv/extractor/cron.log                  # cron-pull activity
tail -f /srv/backups/backup.log                  # last backup run
```

### Roll back to a previous image

If a bad commit shipped to `:latest`:

```bash
# On the VM as extractor user
docker pull ghcr.io/<owner>/openproject-extractor:<previous_sha>
docker tag  ghcr.io/<owner>/openproject-extractor:<previous_sha> \
            ghcr.io/<owner>/openproject-extractor:latest
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    up -d extractor
```

Then push a fix to `main` — the next successful Actions build overwrites `:latest` and the rollback resolves automatically.

### Restore Postgres from backup

```bash
# On the VM as extractor user
gunzip -c /srv/backups/daily/bugs-YYYY-MM-DD.sql.gz | \
    docker compose ... exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Likewise for DataLens (use `datalens-postgres` and `pg_restore` — the DataLens dump is from `pg_dumpall`, restore with `psql`).

### TLS certificate renewal

`certbot.timer` (systemd) auto-renews ~30 days before expiry. After renewal, reload nginx:

```bash
docker compose ... exec nginx nginx -s reload
```

(Or set up a `certbot --deploy-hook` to do this automatically — see `man certbot`.)

### Manual deploy without waiting for cron

```bash
make prod-up         # equivalent to: docker compose ... up -d
```

Pulls and restarts everything that has changed. Useful right after a deploy to skip the up-to-5-minute wait.
```

(End of new sections.)

- [ ] **Step 2: Visual sanity-check**

```bash
git diff README.md | head -100
```

Confirm: the new sections are appended at the end, the markdown table-of-contents (if any) implicitly updates by virtue of the new headings, and no existing content was disturbed.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: production deploy + operations runbook"
```

---

## Task 7: Final smoke checks + push

**Files:** none (verification + push only).

- [ ] **Step 1: Run `make nginx-check`** to confirm nginx config is still valid

```bash
make nginx-check
```

Expected: `nginx: configuration file ... test is successful` and `nginx config OK`.

- [ ] **Step 2: Run `bash tests/test_backup.sh`** (postgres must be up — `make up` first)

```bash
make up
bash tests/test_backup.sh
```

Expected: `PASS: backup.sh produced a valid bugs dump ...`.

- [ ] **Step 3: Confirm the existing test suite still passes**

```bash
make test-integration
```

Expected: 33 passed (no regressions from Phase 4 — we didn't touch any Python).

- [ ] **Step 4: Sanity-check `docker compose config` for the prod stack**

```bash
GHCR_OWNER=lychagin SERVER_NAME=example.com \
    docker compose -f docker-compose.yml \
                   -f docker-compose.datalens.yml \
                   -f docker-compose.prod.yml \
                   config > /tmp/compose-rendered.yml
grep -E '(image:|server_name|ports:)' /tmp/compose-rendered.yml | head -20
```

Expected output includes:
- `image: ghcr.io/lychagin/openproject-extractor:latest` (extractor)
- `image: nginx:alpine`
- `ports:` blocks with `80:80` and `443:443` for nginx

If nothing weird, delete the temp file: `rm /tmp/compose-rendered.yml`.

- [ ] **Step 5: Push everything**

```bash
git push
```

Expected: clean push, `main` is at `origin/main`.

- [ ] **Step 6: Verify the workflow runs on the new commits**

```bash
gh run list --workflow=build-image.yml --limit 3
```

Expected: a recent run for the new commits, status `completed` and `success` (after a couple of minutes). If `failure`, debug with `gh run view <id> --log`.

- [ ] **Step 7: Final state check**

```bash
git log --oneline origin/main..HEAD     # should be empty
git status                              # clean tree
```

---

## Verification (end-to-end)

After running this plan but BEFORE provisioning a real VM:

1. `gh run list --workflow=build-image.yml --limit 5` — every push to main produces a green build.
2. <https://github.com/{owner}?tab=packages> — `openproject-extractor` package is visible (private), with `:latest` and per-commit `:<sha>` tags.
3. `make nginx-check` — nginx template is syntactically valid.
4. `bash tests/test_backup.sh` — backup script produces a valid gzipped dump.
5. `make test-integration` — 33 tests still passing.
6. `make prod-up` (locally, with `GHCR_OWNER` and `SERVER_NAME` set, after `docker login ghcr.io`) — the full stack comes up, pulling the extractor image from ghcr.io rather than building. nginx serves on 80 and 443 (TLS will fail without a real cert, but the container starts).

After provisioning a real VM (out of plan scope, user-driven):

1. `https://<subdomain>/` returns the DataLens login over a valid Let's Encrypt TLS certificate.
2. After admin password change and dashboard re-creation (or DataLens DB restore), `Bugs overview` and `Bug trends` dashboards load.
3. A test push to `main` lands on the VM within 7 minutes.
4. The first 03:00 cron tick produces a backup file in `/srv/backups/daily/`.

## Non-goals (reminder, do NOT implement)

- No Watchtower / external auto-update agent (cron-pull is enough).
- No webhook-based deploy (cron's 5-min latency is acceptable).
- No off-site backup (Q6: local-only).
- No HTTP basic-auth in front of DataLens (Q5: only DataLens admin auth).
- No automatic `:sha` pin promotion (Q3: `:latest` always).
- No multi-arch builds (single linux/amd64).
- No `/healthz` endpoint on the extractor.
- No automatic DataLens stack updates (manual `make datalens-up` to refresh upstream tags).

## Deferred follow-ups (post-plan)

- Pick a VM provider, run `provision-vm.sh`, complete the manual handoff checklist.
- After 1–2 weeks of running, decide whether to add S3 off-site backup. (Adding it = one cron line + AWS CLI install in provision-vm.sh.)
- After 1–3 months, decide whether `:latest`-only is still the right tag strategy or if you want to introduce a manual promote step.
- Consider buying a real domain if the provider subdomain is too ugly. Replacement = one nginx-conf line + one certbot rerun.
