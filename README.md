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

## Tests

```bash
make test               # 9 unit tests, fast, no DB needed
make test-integration   # +8 integration tests, requires `make up` first
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
