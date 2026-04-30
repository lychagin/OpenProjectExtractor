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

## Tests

```bash
make test               # 9 unit tests, fast, no DB needed
make test-integration   # +6 integration tests, requires `make up` first
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
