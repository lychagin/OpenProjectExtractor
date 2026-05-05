# Phase 4 — CI/CD + production deploy on VM (design)

Status: design approved 2026-05-02. Ready for `/writing-plans`.

## Goal

Move the OpenProjectExtractor stack from "runs on dev laptop" to "runs 24/7 on a single Ubuntu VM, accessible to the team via HTTPS." Ship every push to `main` automatically. DataLens dashboards (`Bugs overview`, `Bug trends`) become a public web service for the team.

## Decisions (locked)

| # | Decision | Chosen |
|---|---|---|
| 1 | DataLens placement | (B) On the VM, public access via reverse-proxy. Required because the laptop is offline at night and the team needs constant access. |
| 2 | Image registry & visibility | `ghcr.io`, **private** (matches the private GitHub repo). Free under personal-account quotas at this scale. |
| 3 | Tag strategy | `:latest` always (every `main` push overwrites). `:<sha>` also pushed for ad-hoc rollback. No manual promote step. |
| 4 | Domain | Provider subdomain (option B from brainstorm). User does NOT yet own a domain; will use whatever the VM provider hands out. Caveat in implementation: confirm at provisioning time that the subdomain can have an A-record pointing at the VM IP — without that, Let's Encrypt's HTTP challenge will fail and we'll need to buy a real domain or fall back to DuckDNS. |
| 5 | nginx auth-basic in front of DataLens | No. DataLens own admin auth (with strong password) is the only barrier. Simpler for the team. |
| 6 | Backup destination | Local-only on the VM (rotation: 7 daily + 4 weekly). User accepts the risk of catastrophic VM loss. Adding off-site backup later is one cron-line. |

## VM sizing target

| Resource | Minimum | Comfortable | Notes |
|---|---|---|---|
| RAM | 6 GB | **8 GB** | DataLens stack ~3-5 GB under team load; extractor + Postgres ~500 MB; nginx ~50 MB; OS + headroom ~2 GB. |
| Disk | 40 GB | **50 GB** | Docker images ~5 GB (DataLens) + ~500 MB (extractor, postgres, nginx); Postgres bugs ~1 GB at year 5; DataLens-Postgres ~200 MB; backups ~2 GB rolling; OS ~5 GB; reserve. |
| CPU | 2 vCPU | 4 vCPU | DataLens is 9 containers; team-side dashboard rendering can spike. |
| OS | Ubuntu 24.04 LTS | — | |

User indicated Timeweb's matching tier was "expensive" and is undecided where to host. The VM hosting choice is **not** part of this spec — the spec assumes any VM matching the spec above. The provisioning script targets Ubuntu 24.04 LTS regardless of provider.

## Architecture

```
                                       VM (8GB / 50GB / Ubuntu 24.04 LTS, 24/7)
                                       ┌──────────────────────────────────────────┐
team browser ──HTTPS──▶                │  nginx :443 (TLS via Let's Encrypt)      │
   (https://subdomain/)                │       │                                  │
                                       │       ▼                                  │
                                       │  DataLens UI :8080  (internal)           │
                                       │       │                                  │
                                       │       ▼                                  │
                                       │  DataLens-Postgres (dashboards)          │
                                       │  + 7 other DataLens services             │
                                       │                                          │
                                       │  extractor → Postgres bugs               │
                                       │       (5min sync loop, FK to bug_history)│
                                       │                                          │
                                       │  cron(extractor):                        │
                                       │    - pull ghcr.io image every 5 min      │
                                       │    - pg_dump backups 03:00 daily         │
                                       └──────────────────────────────────────────┘
                                       Open ports: 22 (SSH), 80 (HTTP→redirect),
                                                   443 (HTTPS only)
                                       Closed externally: 5432, 8080, all internal

        ┌─────────────────────────────┐
git push│ GitHub Actions              │
─main──▶│   build image →             │      ghcr.io (private)
        │   docker login ghcr.io →    │ ───▶ openproject-extractor:latest
        │   push :latest + :<sha>     │ ───▶ openproject-extractor:<sha>
        └─────────────────────────────┘            ▲
                                                   │
                                          docker compose pull
                                          (cron, every 5 min)

OpenProject API ◀──HTTPS── extractor (uses .cert/bundle.pem)
```

Data path is unchanged from Phase 1-3. New on the VM: nginx + TLS + DataLens-stack + cron-driven pull/backup.

## Components

### 4.1 Build & publish workflow — `.github/workflows/build-image.yml`

Triggers: `push` to `main`, manual `workflow_dispatch`.

```yaml
name: Build and push image
on:
  push: { branches: [main] }
  workflow_dispatch:
permissions:
  contents: read
  packages: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
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
            ghcr.io/${{ github.repository_owner }}/openproject-extractor:latest
            ghcr.io/${{ github.repository_owner }}/openproject-extractor:${{ github.sha }}
```

No additional secrets to configure: `GITHUB_TOKEN` is auto-provided per workflow run with `packages: write` permission. The image inherits the repo's private visibility automatically.

Build time expectation: ~1-2 min (cached Python base layer; only the `src/` and `db/` layers rebuild on most pushes).

### 4.2 Production compose override — `docker-compose.prod.yml`

```yaml
services:
  extractor:
    build: !reset null
    image: ghcr.io/<owner>/openproject-extractor:latest
    pull_policy: always
  nginx:
    image: nginx:alpine
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
    depends_on: [ui]
    restart: unless-stopped
```

`<owner>` resolved at provisioning time (templated via env-var substitution or sed). Brought up with:

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.datalens.yml \
               -f docker-compose.prod.yml up -d
```

Dev workflow unchanged — `make up` still uses the original two compose files with `build: .`.

### 4.3 nginx configuration — `nginx/conf.d/datalens.conf`

```nginx
server {
    listen 443 ssl http2;
    server_name <subdomain>;
    ssl_certificate     /etc/letsencrypt/live/<subdomain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<subdomain>/privkey.pem;

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
    }
}

server {
    listen 80;
    server_name <subdomain>;
    return 301 https://$host$request_uri;
}
```

`<subdomain>` substituted at provisioning. The `ui` hostname resolves through the docker compose network shared with the DataLens stack.

### 4.4 VM provisioning — `scripts/provision-vm.sh`

Idempotent shell script run as root on a fresh Ubuntu 24.04 LTS VM. Sections:

1. **Base packages**: `apt update`/`upgrade`, install `docker-ce`, `docker-compose-plugin`, `certbot`, `ufw`, `fail2ban`.
2. **User**: create `extractor` system user, add to `docker` group, create `/srv/extractor` and `/srv/backups` (mode 750, owned by extractor).
3. **Firewall**: `ufw` default-deny in, allow 22/80/443, enable.
4. **Code**: clone the GitHub repo into `/srv/extractor` (via HTTPS for now; SSH key setup if private repo and clone fails).
5. **Manual handoff**: print instructions for items the script cannot do automatically:
   - `scp` `.env` to `/srv/extractor/.env` (chmod 600).
   - `scp` `.cert/bundle.pem` to `/srv/extractor/.cert/` (chmod 600).
   - Run `docker login ghcr.io -u <user>` with a personal access token (`read:packages` scope).
   - Run `certbot certonly --standalone -d <subdomain>` (port 80 must be free; nginx not yet running).
6. **First start**: prompt user to run the full-stack `docker compose ... up -d` once secrets and certs are in place.

### 4.5 Cron-driven pull and backup

Crontab on the `extractor` user:

```cron
# Pull latest extractor image every 5 minutes; restart container only if image changed.
*/5 * * * * cd /srv/extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml pull --quiet extractor && docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml up -d extractor

# Daily Postgres dumps at 03:00.
0 3 * * * /srv/extractor/scripts/backup.sh
```

Update latency: ≤5 minutes from `git push` to live. Cron-pull bandwidth: ~1 KB manifest check per tick when nothing changed; ~10-15 MB layer pull when a new image lands.

### 4.6 Backup script — `scripts/backup.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/srv/backups
DAILY=$BACKUP_DIR/daily
WEEKLY=$BACKUP_DIR/weekly
mkdir -p "$DAILY" "$WEEKLY"

DATE=$(date +%Y-%m-%d)
DOW=$(date +%u)   # 1=Mon .. 7=Sun

cd /srv/extractor
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$DAILY/bugs-$DATE.sql.gz"
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    exec -T datalens-postgres pg_dumpall -U postgres | gzip > "$DAILY/datalens-$DATE.sql.gz"

# Promote Sunday's daily to weekly
if [ "$DOW" = "7" ]; then
    cp "$DAILY/bugs-$DATE.sql.gz"     "$WEEKLY/bugs-$DATE.sql.gz"
    cp "$DAILY/datalens-$DATE.sql.gz" "$WEEKLY/datalens-$DATE.sql.gz"
fi

# Rotation: keep 7 daily, 4 weekly
find "$DAILY" -name '*.sql.gz' -mtime +7 -delete
find "$WEEKLY" -name '*.sql.gz' -mtime +28 -delete
```

Secrets (`POSTGRES_USER`, `POSTGRES_DB`) are read from `/srv/extractor/.env` via the `extractor`-user environment (script sources `.env` at the top).

### 4.7 Rollback procedure (manual)

When a bad commit ships to `:latest`:

```bash
# On the VM as extractor user
docker pull ghcr.io/<owner>/openproject-extractor:<previous_sha>
docker tag  ghcr.io/<owner>/openproject-extractor:<previous_sha> \
            ghcr.io/<owner>/openproject-extractor:latest
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    up -d extractor
```

Then fix `main`, push — next successful build overwrites `:latest` and rollback resolves automatically.

Documented in `README.md` under a new "Production rollback" section.

## Test plan

This phase is mostly operational — most artifacts are config files (compose, nginx conf, shell scripts) rather than testable Python code. Where automated tests are possible:

1. **Workflow smoke test**: pushing to a feature branch + manual `workflow_dispatch` confirms the GH Actions workflow builds and pushes successfully (verified by checking ghcr.io UI for the new tag).
2. **Backup script**: a `tests/test_backup.sh` shell test that runs `backup.sh` against the local dev compose stack, asserts a `.sql.gz` file appears in `/tmp/test-backups/`, and asserts the gzip is valid (`gzip -t`).
3. **provision-vm.sh idempotence**: documented as "run on a throwaway VM, then re-run; second run should produce no errors and no diff in `/srv/extractor`."

End-to-end smoke (manual, after provisioning):

- `https://<subdomain>/` returns the DataLens login page over a valid TLS cert.
- After login with the changed admin password, `Bugs overview` and `Bug trends` dashboards load.
- `docker compose logs extractor` shows the cycle counter incrementing every 5 minutes.
- A test push to `main` results in a new image on `ghcr.io` within 2 minutes, and the VM picks it up within 5 minutes (verify via `docker inspect` image SHA before/after).
- `pg_dump` backup file appears in `/srv/backups/daily/` after the 03:00 cron tick.

## Non-goals

- **No Kubernetes.** Single-VM docker-compose, per project memory.
- **No Watchtower / external auto-update agent.** Cron-pull is enough.
- **No webhook-based deploy.** Push notifications from GH would be lower-latency but require an inbound port; cron's 5-min latency is acceptable.
- **No off-site backup** in this phase. Local-only per Q6. Adding S3 later is one cron-line.
- **No HTTP basic-auth** in front of DataLens. Per Q5.
- **No `:sha` pin for production.** Tag strategy is `:latest`-only per Q3. `:<sha>` tags are pushed but not consumed by the VM unless someone manually retags for rollback.
- **No multi-arch builds.** Single linux/amd64 image; VM provider's machine is amd64.
- **No `/healthz` endpoint on the extractor.** Per roadmap deferred-follow-up; can add later if monitoring is needed.
- **No DataLens stack auto-update.** DataLens images ride on whatever tags are pinned in `docker-compose.datalens.yml`. Updates are a manual `make datalens-up` (with prior `docker compose ... pull` against the upstream tags).

## Deferred follow-ups

- **VM provider choice**: User said Timeweb came out expensive; will pick a different one. Spec is provider-agnostic. Note in plan: confirm provider's subdomain offering supports A-records before depending on Let's Encrypt HTTP challenge.
- **DataLens admin password**: change `admin/admin` immediately after first login. Document in README under "Operations" section.
- **Off-site backup**: monitor whether local-only is enough; revisit after the first 3 months of accumulated `bug_history` is non-trivial to lose.
- **Domain purchase**: if the provider subdomain is too ugly for client-facing dashboard URLs, buy a real domain. Replacing it is one nginx-conf line + one certbot rerun.
- **Monitoring**: cron-pull failures are silent today. If the system runs unattended for weeks, consider a `/healthz` endpoint + Uptime Kuma elsewhere. Out of scope here.

## Implementation order (rough — refined in `/writing-plans`)

1. **Workflow** (4.1) — completely independent of the VM. Push, see image land in ghcr.io. PR-ready in isolation.
2. **Compose override + nginx config** (4.2 + 4.3) — testable locally first via `docker compose -f ... -f docker-compose.prod.yml up` against a self-signed cert.
3. **provision-vm.sh + backup.sh + rollback doc** (4.4 + 4.6 + 4.7) — written in repo, exercised when the VM is finally chosen.
4. **End-to-end on real VM** (4.5 cron + first deploy) — once user picks a provider and VM is up, run provision script, scp secrets, certbot, first compose-up, smoke tests.
5. **README "Operations" section** documenting all of the above.
