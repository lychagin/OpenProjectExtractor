# OpenProjectExtractor

Pulls bugs out of an [OpenProject](https://www.openproject.org/) instance into Postgres on a 5-minute loop, with DataLens (OSS) on top for dashboards.

```
┌──────────────┐    HTTPS     ┌────────────────┐   SQL    ┌──────────┐
│ OpenProject  │  ◀────────   │  extractor     │  ─────▶  │ Postgres │  ◀──── DataLens (UI on :8080)
│   API        │   Basic      │  (5-min loop)  │   UPSERT │  (bugs)  │        connects via the shared
└──────────────┘   apikey:tok │                │  + history└──────────┘        compose network.
                              └────────────────┘
```

`bugs` keeps the current state (one row per bug, with denormalized status / assignee / priority and a `raw jsonb` snapshot). `bug_history` is append-only — a new row whenever OpenProject's `lockVersion` changes for that bug, so you can chart trends over time.

## Prerequisites

- Docker + Docker Compose v2 plugin
- Python 3.12 + venv (only for running tests / extractor on the host; the dockerized stack doesn't need it)
- An OpenProject API token (project secrets → personal access token, or admin → API tokens)
- If your OpenProject server's TLS chain is missing intermediates (some self-managed installs are): the missing intermediate CA, concatenated with the `certifi` bundle, dropped at `.cert/bundle.pem`. The compose stack mounts `./.cert/` read-only into the extractor container.

## Setup

```bash
cp .env.example .env
# edit .env — fill OPENPROJECTTOKEN and (optionally) change POSTGRES_PASSWORD
```

Optional Python venv for host-side commands (`make test`, `make show-bug`, `make dry-run`):

```bash
python3 -m venv .venv
make install
```

## Run the extractor stack (light: 2 services)

```bash
make up      # postgres + extractor
make logs    # follow extractor logs (cycle counters every 5min)
make once    # run a single sync cycle (one-shot, doesn't loop)
make psql    # psql shell against the bugs DB
make down    # stop everything (volume `pgdata` is preserved)
```

Postgres is exposed on host port **5433** (not 5432) so it doesn't collide with any host-side postgres. From the host:

```bash
PGPASSWORD=changeme psql -h localhost -p 5433 -U extractor -d extractor
```

## Run the full stack (extractor + DataLens, ~11 services)

```bash
make datalens-up    # adds 9 DataLens services on top
make datalens-logs  # follow ui / us logs
make datalens-down  # stop the full stack
```

DataLens is heavy: ~3-5 GB of images on first pull, ~2-4 GB RAM at runtime. Bring it up only when you're working with dashboards.

### First-time DataLens connection setup (~30 seconds, do this once)

1. Open <http://localhost:8080> — login as **`admin`** / **`admin`**.
2. *Connections* → *Create connection* → *PostgreSQL*.
3. Fill in:
   - **Hostname**: `postgres` (the extractor's DB service — reachable inside the docker network)
   - **Port**: `5432`
   - **Database**: `extractor`
   - **Username**: `extractor`
   - **Password**: whatever you set as `POSTGRES_PASSWORD` in `.env`
   - leave SSL off
4. *Test connection* → *Create*.
5. Now create a *Dataset* on the `bugs` table (or `bug_history` for trend charts) and start building charts.

The connection lives in DataLens's own Postgres (`datalens-postgres` container, separate from the extractor's `postgres`), so it survives `make datalens-down` / `up`.

For production, replace the default `admin` / `admin` by running upstream's `init.sh --hc` script — see <https://github.com/datalens-tech/datalens>.

#### Gotcha: switching from `make up` to `make datalens-up`

If you started with the light stack (`make up`) and then bring the full stack with `make datalens-up`, the existing `postgres` container keeps its old network aliases, leaving DataLens services unable to resolve `postgres` by hostname (the extractor itself crashloops with `failed to resolve host 'postgres'`). Recreate it once after the switch:

```bash
docker compose -f docker-compose.yml -f docker-compose.datalens.yml up -d --force-recreate postgres extractor
```

Going from cold (`make datalens-up` on an empty docker state) doesn't hit this — the issue is specifically about reusing a container created under a narrower network configuration.

### First dashboard

Once the connection is configured, build a dataset and a dashboard:

1. *Datasets* → *+ New* → connection `extractor-bugs` → drag table `v_bugs` onto the canvas → *Save* as `bugs`.
2. Build five charts from the `bugs` dataset:
   - **Bugs by status** — pie, color = `status_name`, measure = `count(id)`.
   - **Bugs by priority** — pie, color = `priority_name`, measure = `count(id)`.
   - **Bug load by assignee** — horizontal bar (Линейчатая диаграмма), Y = `assignee_name`, X = `count(id)`, sort = `count(id) DESC`, filter `assignee_name IS NOT NULL`. To approximate top-N (DataLens OSS has no built-in top-N filter at time of writing): add a measure filter like `count([id]) > 30` and tune the threshold by eye.
   - **Open vs closed** — pie, color = `is_closed`. (`is_closed` is true for OpenProject statuses `Closed`, `No issue found`, `Rejected` — per the `v_bugs` view definition.)
   - **New bugs per week** — line, X = `op_created_at` grouped by week, Y = `count(id)`, filter to last 12 months.
3. *Dashboards* → *+ New* → drop the 5 charts onto the grid → *Save* as `Bugs overview`. Suggested layout: 3 small pies in row 1 (4 cols each), full-width assignee bar in row 2, full-width weekly line in row 3.

The dashboard config lives in DataLens's own Postgres (the `datalens-postgres` container, in `pg-us-db`), so it survives `make datalens-down` / `up` cycles. To reset, use `docker compose -f docker-compose.yml -f docker-compose.datalens.yml down -v` (the `-v` drops the named volume).

### Bug trends dashboard

A second dashboard fed by SQL views over `bug_history`. Build it after `Bugs overview` is up.

1. *Datasets* → *+ New* → connection `extractor-bugs` → drag in each of these views, one dataset each:
   - `v_bug_status_weekly` — save as `bug_status_weekly`
   - `v_bug_throughput_weekly` — save as `bug_throughput_weekly`
   - `v_bug_time_in_status` — save as `bug_time_in_status`
2. Build three charts:
   - **Bug status mix over time** — stacked area, X = `week_start`, Y = sum(`bug_count`), color = `status_name`. Pin closed statuses to the bottom of the stack for legibility.
   - **Bug throughput per week** — line, X = `week_start`, Y = sum(`event_count`), color = `event_type` (two series: `opened`, `closed`).
   - **Average time in status** — horizontal bar, Y = `status_name`, X = avg(`days_in_status`), sort desc, measure filter `count([days_in_status]) > 5`.
3. *Dashboards* → *+ New* → drop charts onto the grid, suggested layout: status mix full-width row 1, throughput full-width row 2, time-in-status (8 cols) + a text widget with the legend (4 cols) in row 3. Save as `Bug trends`.

Caveat: history is collected forward from when the extractor first ran (~2026-04-30). Trends before that date are not reconstructable. Reopens (Closed → In progress → Closed) count as separate close events. The closed-set is `Closed`, `No issue found`, `Rejected` (per `is_status_closed()` in the DB, used by `v_bugs.is_closed` too).

#### Gotcha: DataLens auto-aggregates integers as `sum`

When you drop an integer column (`id`, `bug_count`, `event_count`, etc.) into the Y axis, DataLens defaults the aggregation to `sum`. For a "how many bugs?" chart that's wrong — `sum([id])` is the sum of primary keys, not a count of rows. Use `count([id])` instead, either by changing the field's aggregation function inline (click the Y pill → switch from `Sum` to `Count`) or by making a calculated measure `bug_count = COUNT([id])` in the dataset.

## Tests

```bash
make test               # 9 unit tests, fast, no DB needed
make test-integration   # +24 integration tests (8 db + 16 history-views), requires `make up` first
```

Integration tests run all DML inside a single transaction that gets rolled back at teardown — safe to run against a populated production DB without losing data.

## Debug helpers

```bash
make show-bug ID=6832   # pretty-prints one work-package as JSON
make dry-run            # fetch from OpenProject without touching the DB
```

## CI

`.github/workflows/extract-bugs.yml` runs on push and weekly cron — unit tests + a `--dry-run` smoke fetch (no DB needed in CI). Real syncing happens on the VM, not in Actions.

## Repo layout

```
src/
  client.py     OpenProject HTTP client (paginates, returns raw work-packages).
  db.py         Postgres schema bootstrap + upsert / history / soft-delete.
  sync.py       run_sync_cycle — one full pass.
  main.py       Entry point: argparse with --once and --dry-run, sleep loop otherwise.
  show_bug.py   Debug helper for `make show-bug ID=<n>`.
db/migrations/  Idempotent SQL applied on every container start.
tests/          test_client (unit) + test_db (integration, opt-in via --integration).
docker-compose.yml          Light stack: postgres + extractor.
docker-compose.datalens.yml DataLens stack, vendored from upstream (see header for changes).
scripts/vendor-datalens.sh  Re-vendor DataLens compose from upstream.
```

## OpenProject API quirks worth remembering

- Auth is HTTP **Basic** with username `apikey` and the token as password — not Bearer.
- The `type` filter in the work-packages endpoint takes **type IDs**, not names. We resolve `Bug` → id via `/api/v3/projects/<id>/types` on each cycle.
- Pagination uses `offset`/`pageSize`/`total` at the response root, not `_meta.count` and `_links.next`.
- Field/relation names like `status`, `assignee`, `priority` live in `_links.<rel>.title` (HAL), not as scalar fields. We denormalize them into columns on `bugs` so DataLens can chart them without parsing JSON.

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

Likewise for DataLens — the dump is from `pg_dumpall` (plain SQL), so restore with `psql` against the `datalens-postgres` service in the same pipe pattern as above.

### TLS certificate renewal

`certbot.timer` (systemd) auto-renews ~30 days before expiry. `provision-vm.sh` installs a deploy hook at `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` that reloads nginx inside its container after each renewal — no manual action needed.

To force an immediate nginx reload (e.g. after manually copying a cert):

```bash
docker compose -f docker-compose.yml -f docker-compose.datalens.yml -f docker-compose.prod.yml \
    exec nginx nginx -s reload
```

### Manual deploy without waiting for cron

```bash
make prod-up         # equivalent to: docker compose ... up -d
```

Pulls and restarts everything that has changed. Useful right after a deploy to skip the up-to-5-minute wait.

### Stop the stack (e.g., for maintenance)

```bash
make prod-down
```

Stops all services. The named Postgres volumes (extractor's `pgdata`, DataLens's `pg-us-db`) survive — bring everything back with `make prod-up`.
