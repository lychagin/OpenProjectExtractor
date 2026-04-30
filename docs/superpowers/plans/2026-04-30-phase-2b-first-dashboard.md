# Phase 2b — First DataLens Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a first dashboard in DataLens with five charts (status / priority / assignee / open-vs-closed / created-per-week) drawn from a clean Postgres view of the extractor's `bugs` table.

**Architecture:** Add a SQL migration (`db/migrations/002_views.sql`) that exposes a `v_bugs` view filtering soft-deleted rows and pre-computing `is_closed` based on OpenProject's official isClosed flag (`Closed`, `No issue found`, `Rejected`). Bring up the full compose stack (extractor + Postgres + 9 DataLens services). In DataLens UI: register a Postgres connection to the extractor's DB, create a dataset on `v_bugs`, build five charts, compose them into one dashboard. README documents the manual UI steps so the dashboard is reproducible from scratch.

**Tech Stack:** PostgreSQL 16 (view), psycopg 3 (test fixture), Docker Compose v2 (full stack), DataLens OSS 2.9.0 (UI).

---

## File Structure

**Created:**
- `db/migrations/002_views.sql` — `v_bugs` view definition. Idempotent (`CREATE OR REPLACE VIEW`). Bootstrap_schema picks it up on every container start.
- `tests/test_db.py` — append integration tests for the view (filters deleted, derives `is_closed`).

**Modified:**
- `README.md` — add a "First dashboard" subsection under the existing "Run the full stack" heading, documenting the connection / dataset / 5 charts / dashboard composition steps.

**Not modified:**
- `src/db.py` — bootstrap_schema already iterates all `db/migrations/*.sql` lexicographically; nothing new to wire.
- `docker-compose*.yml`, `Makefile` — already covered by Phase 2a.

---

## Pre-flight: Resource check (no actions required)

The extractor stack is down (`make down` ran at end of Phase 2a). Before bringing up DataLens:

- **Disk:** ~5 GB needed (9 images). Current free: 894 GB. **No prune needed.** Existing `infra.*` / `docker-*` / `cr.selcloud.ru/*` images on this host belong to active terra-housing-mgmt and predubezhdai projects and **must not be deleted**.

- **RAM:** ~2-4 GB needed for DataLens runtime. Current available: 4.8 GB. **Tight.** Recommend closing heavy host apps (VS Code with big projects, browsers with many tabs) before `make datalens-up`. If OOM-killing happens, raise WSL2 limit in `~/.wslconfig` (`memory=12GB`).

- **Optional safe cleanup if you really want more headroom:** `docker builder prune -f --filter until=168h` drops build cache older than 7 days. **Only build cache, no images touched** — terra-housing rebuilds as normal but skips cache hits on very old layers. Frees ~10-20 GB. Skip otherwise.

---

## Task 1: Add the `v_bugs` view migration

**Files:**
- Create: `db/migrations/002_views.sql`
- Modify: `tests/test_db.py` — append two new test functions at end of file

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py` (after the last existing test, before the file ends):

```python
def test_v_bugs_view_hides_soft_deleted(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    db.upsert_bug(db_conn, _wp(id=2))
    db.mark_unseen_as_deleted(db_conn, [1])  # bug id=2 is now soft-deleted

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_bugs ORDER BY id")
        ids = [r[0] for r in cur.fetchall()]
    assert ids == [1]


def test_v_bugs_view_derives_is_closed(db_conn):
    closed_status = {"href": "/api/v3/statuses/9", "title": "Closed"}
    no_issue_status = {"href": "/api/v3/statuses/10", "title": "No issue found"}
    rejected_status = {"href": "/api/v3/statuses/11", "title": "Rejected"}
    open_status = {"href": "/api/v3/statuses/7", "title": "In progress"}

    for i, st in enumerate([closed_status, no_issue_status, rejected_status, open_status], start=1):
        wp = _wp(id=i)
        wp["_links"]["status"] = st
        db.upsert_bug(db_conn, wp)

    with db_conn.cursor() as cur:
        cur.execute("SELECT id, is_closed FROM v_bugs ORDER BY id")
        rows = cur.fetchall()
    # ids 1,2,3 → Closed/No issue found/Rejected → is_closed=True
    # id 4 → In progress → is_closed=False
    assert rows == [(1, True), (2, True), (3, True), (4, False)]
```

- [ ] **Step 2: Run tests, verify they fail (view does not exist yet)**

Stack must be up for integration tests:

```bash
make up
```

Wait for postgres healthy (~5s), then:

```bash
make test-integration
```

Expected: 2 new tests fail with `psycopg.errors.UndefinedTable: relation "v_bugs" does not exist`. Other 13 tests still pass.

- [ ] **Step 3: Create the migration**

Write `db/migrations/002_views.sql`:

```sql
-- View consumed by DataLens datasets.
-- Idempotent (CREATE OR REPLACE); runs on every container start.

CREATE OR REPLACE VIEW v_bugs AS
SELECT
    *,
    status_name IN ('Closed', 'No issue found', 'Rejected') AS is_closed
FROM bugs
WHERE deleted_at IS NULL;

COMMENT ON VIEW v_bugs IS
    'Current bug state for DataLens: hides soft-deletes, adds is_closed (per OpenProject isClosed flag: Closed / No issue found / Rejected).';
```

- [ ] **Step 4: Re-run bootstrap to apply the new migration**

`bootstrap_schema` runs every time the extractor container starts. Trigger it:

```bash
make once
```

Expected: log line `applied migration: 002_views.sql` followed by the usual sync cycle.

- [ ] **Step 5: Run tests again, verify they pass**

```bash
make test-integration
```

Expected: 15 tests pass (9 unit + 6 prior integration + 2 new view tests = 17 total). If the count is 15, double-check the test file got the appended tests.

Wait — count check: prior was 9 unit + 6 integration = 15. Adding 2 new = 17. Output should show `17 passed`.

- [ ] **Step 6: Spot-check the view from psql**

```bash
make psql
```

Inside psql:

```sql
SELECT count(*) FILTER (WHERE is_closed) AS closed,
       count(*) FILTER (WHERE NOT is_closed) AS open,
       count(*) AS total
FROM v_bugs;
\q
```

Expected: numbers consistent with the live data (e.g. ~700 open, ~60 closed for the wone-it project — exact split depends on the moment).

- [ ] **Step 7: Commit**

```bash
git add db/migrations/002_views.sql tests/test_db.py
git commit -m "$(cat <<'EOF'
feat(db): add v_bugs view with derived is_closed flag

- db/migrations/002_views.sql: v_bugs view filters soft-deleted rows
  and adds is_closed boolean derived from status_name (Closed /
  No issue found / Rejected — per OpenProject's isClosed flag, verified
  via the status admin screen).
- tests/test_db.py: two integration tests covering the filter and the
  derived flag.

DataLens datasets will use v_bugs (not bugs) so deleted rows never
leak into dashboards and "open vs closed" charts can group on the
already-computed flag.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

Expected: commit lands, push succeeds.

---

## Task 2: Bring up the full DataLens stack

User-driven: this consumes ~1.5-2 GB of network bandwidth and 2-4 GB of RAM. Wait for explicit "go".

**Files:** none (operational only)

- [ ] **Step 1: Confirm the user wants to proceed**

The plan should pause here. The previous chat established that downloading begins **only on user command**. Do not run the next step preemptively.

- [ ] **Step 2: Pull images and start the full stack**

```bash
make datalens-up
```

Expected: ~10-15 minutes on first run for the image pull; ~1 minute for all containers to settle into healthy state.

- [ ] **Step 3: Verify all 11 services are healthy**

```bash
docker compose -f docker-compose.yml -f docker-compose.datalens.yml ps
```

Expected: 11 services (`auth`, `control-api`, `data-api`, `datalens-postgres`, `extractor`, `meta-manager`, `postgres`, `temporal`, `ui`, `ui-api`, `us`) — all `Up`. Postgres-like services should show `(healthy)`. UI logs may take a minute extra to settle:

```bash
docker compose -f docker-compose.yml -f docker-compose.datalens.yml logs ui --tail=20
```

Expected: no fatal errors, the Node process reports it's listening on `:8080`.

- [ ] **Step 4: Confirm UI is reachable**

Open `http://localhost:8080` in a browser. Expected: a login page (admin/admin).

If the UI doesn't load: check `make datalens-logs` — usually it's `us` waiting on `datalens-postgres` to finish initial schema setup; give it another minute.

---

## Task 3: Register Postgres connection in DataLens

**Files:** none (UI work, persisted in DataLens's own metadata Postgres)

The exact menu names may differ slightly across DataLens minor versions. The intent is: **add a PostgreSQL connection pointing at the extractor's `postgres` service** (reachable on the shared docker network as hostname `postgres:5432`).

- [ ] **Step 1: Log in**

Navigate to `http://localhost:8080`. Sign in:
- Username: `admin`
- Password: `admin`

(Ignore "Change password" prompts if any — this is a local dev stack.)

- [ ] **Step 2: Open Connections**

Top nav: *Connections* → button *Create connection* (or *+ New*).

- [ ] **Step 3: Choose PostgreSQL**

In the connector chooser tile grid, select **PostgreSQL**. (Not "ClickHouse", not "PostgreSQL ODBC".)

- [ ] **Step 4: Fill connection form**

| Field | Value |
|---|---|
| Hostname | `postgres` |
| Port | `5432` |
| Database | `extractor` |
| Username | `extractor` |
| Password | (the value of `POSTGRES_PASSWORD` from `.env` — default `changeme`) |
| SSL | off |
| Cache TTL | leave default |

Connection name (top of form): `extractor-bugs`.

- [ ] **Step 5: Test and save**

Press *Test connection*. Expected: green "Connection successful".

If it fails:
- "Could not resolve host" → DataLens services are on a different network than `postgres`. Check `docker network ls` and confirm both compose files declare the same default network. Should be automatic via `docker compose -f -f`.
- "Password authentication failed" → mismatched POSTGRES_PASSWORD between .env and the running postgres container. `make down -v && make datalens-up` reinitializes.

Press *Create*.

---

## Task 4: Create dataset on `v_bugs`

**Files:** none (UI)

DataLens datasets are an abstraction layer over a connection — they expose specific tables/views as fields with types, ready for charts.

- [ ] **Step 1: Create dataset from the connection**

From the `extractor-bugs` connection page, button *Create dataset*. Or top-level: *Datasets* → *+ New* → choose `extractor-bugs` connection.

- [ ] **Step 2: Add `v_bugs` as the source**

In the dataset editor's source picker, find schema `public` and drag `v_bugs` onto the canvas (or click *+ Add source*).

- [ ] **Step 3: Verify field auto-detection**

DataLens introspects column types. Verify these key fields exist with correct types:
- `id` — Integer (Measure or Dimension, doesn't matter)
- `subject` — String
- `status_name` — String
- `priority_name` — String
- `assignee_name` — String
- `is_closed` — Boolean
- `op_created_at` — DateTime
- `created_at` — likely missing (we don't have it on bugs); OK
- `lock_version` — Integer

If any type is wrong (e.g. `op_created_at` shown as String), click the field, change type to *Date and time*.

- [ ] **Step 4: Save dataset**

Top right: *Save*. Name: `bugs`.

---

## Task 5: Chart 1 — Bugs by status (pie)

**Files:** none (UI)

- [ ] **Step 1: Create new chart**

Top nav: *Charts* → *+ Create chart*. Choose dataset `bugs`.

- [ ] **Step 2: Configure**

| Slot | Field |
|---|---|
| Visualization type | **Pie** |
| Color (Цвет) | `status_name` |
| Measure (Показатели) | `id` aggregated as `Count` |

- [ ] **Step 3: Verify**

Preview should show a pie with ~20 slices (one per status). Largest slices: probably `New` and `In progress`. Total: matches `SELECT count(*) FROM v_bugs` (~700+).

- [ ] **Step 4: Save**

Title: `Bugs by status`. *Save*.

---

## Task 6: Chart 2 — Bugs by priority (pie)

**Files:** none (UI)

- [ ] **Step 1: Duplicate previous chart approach**

*+ Create chart* → dataset `bugs` → Pie.

- [ ] **Step 2: Configure**

| Slot | Field |
|---|---|
| Color | `priority_name` |
| Measure | `id` Count |

- [ ] **Step 3: Save**

Title: `Bugs by priority`.

---

## Task 7: Chart 3 — Top assignees (bar, top-10)

**Files:** none (UI)

- [ ] **Step 1: Create chart**

*+ Create chart* → dataset `bugs` → **Bar chart** (vertical or horizontal — horizontal reads better for names).

- [ ] **Step 2: Configure**

| Slot | Field |
|---|---|
| Y-axis (Bars / dimension) | `assignee_name` |
| X-axis (Measure) | `id` Count |
| Sort | by Count DESC |
| Top-N filter | 10 (drop assignees beyond top 10) |

DataLens's "limit by axis": in the dimension's config, set *Top values: 10*.

- [ ] **Step 3: Optional — exclude unassigned**

`assignee_name` will be NULL for bugs without assignee, often appearing as the largest bar. Add a filter: `assignee_name IS NOT NULL` (or `assignee_name != ''`).

- [ ] **Step 4: Save**

Title: `Top 10 assignees`.

---

## Task 8: Chart 4 — Open vs closed (pie)

**Files:** none (UI)

- [ ] **Step 1: Create chart**

*+ Create chart* → dataset `bugs` → Pie.

- [ ] **Step 2: Configure**

| Slot | Field |
|---|---|
| Color | `is_closed` |
| Measure | `id` Count |

- [ ] **Step 3: Tweak labels**

Default labels will be `true` / `false`. Add data labels showing absolute count (and optionally percentage). For nicer legend:
- Click `is_closed` in Color → *Format* → set values: `true` → "Closed", `false` → "Open" (DataLens supports per-value display labels).

- [ ] **Step 4: Save**

Title: `Open vs closed`.

---

## Task 9: Chart 5 — Bugs created per week (line/bar)

**Files:** none (UI)

- [ ] **Step 1: Create chart**

*+ Create chart* → dataset `bugs` → **Line chart** (or column).

- [ ] **Step 2: Configure**

| Slot | Field |
|---|---|
| X-axis | `op_created_at` truncated to **Week** |
| Y-axis (measure) | `id` Count |

In DataLens, click `op_created_at` after dragging to X → *Date grouping* → *Week*.

- [ ] **Step 3: Filter to recent period**

Optional but recommended: filter the X axis to the last 12 months so you see actual trend, not the full 5-year history.

Add filter: `op_created_at >= now() - interval '1 year'` (DataLens has a relative date picker that does this without raw SQL).

- [ ] **Step 4: Save**

Title: `New bugs per week`.

---

## Task 10: Compose dashboard

**Files:** none (UI)

- [ ] **Step 1: Create dashboard**

Top nav: *Dashboards* → *+ Create dashboard*. Name: `Bugs overview`.

- [ ] **Step 2: Add the 5 charts**

In edit mode, *+ Add* → *Chart* → select each of the 5 saved charts, dropping them onto the canvas grid.

Suggested layout (grid is 12-col):
- Row 1 (3 small): `Bugs by status` (4 cols) | `Bugs by priority` (4 cols) | `Open vs closed` (4 cols)
- Row 2: `Top 10 assignees` (12 cols)
- Row 3: `New bugs per week` (12 cols)

- [ ] **Step 3: Save**

Top right: *Save*. Toggle to *Public link* if you want a shareable URL (only works while compose is up).

- [ ] **Step 4: Eyeball the result**

Sanity checks against the data:
- `Bugs by status` total + `Open vs closed` total should match.
- `Top 10 assignees` sum should be ≤ status total.
- `New bugs per week` rightmost bar (current week) is partial — that's expected, not a bug.

If numbers don't add up: most likely the dataset has stale cache. *Settings → Refresh cache* on the dataset, or just wait a minute.

---

## Task 11: Document the dashboard in README

**Files:**
- Modify: `README.md` — append a "First dashboard" section under the existing DataLens block.

- [ ] **Step 1: Append to README**

After the existing "First-time DataLens connection setup" section in `README.md`, append:

```markdown
### First dashboard

Once the connection is configured, build a dataset and a dashboard:

1. *Datasets* → *+ New* → connection `extractor-bugs` → drag table `v_bugs` onto the canvas → *Save* as `bugs`.
2. Build five charts from the `bugs` dataset:
   - **Bugs by status** — pie, color = `status_name`, measure = `count(id)`.
   - **Bugs by priority** — pie, color = `priority_name`, measure = `count(id)`.
   - **Top 10 assignees** — bar, dimension = `assignee_name`, top-N = 10, filter `assignee_name IS NOT NULL`.
   - **Open vs closed** — pie, color = `is_closed`. (`is_closed` is true for OpenProject statuses `Closed`, `No issue found`, `Rejected` — per the `v_bugs` view definition.)
   - **New bugs per week** — line, X = `op_created_at` grouped by week, Y = `count(id)`, filter to last 12 months.
3. *Dashboards* → *+ New* → drop the 5 charts onto the grid → *Save* as `Bugs overview`.

The dashboard config lives in DataLens's own Postgres (the `datalens-postgres` container's `pg-us-db`), so it survives `make datalens-down` / `up` cycles. To reset, use `docker compose -f docker-compose.yml -f docker-compose.datalens.yml down -v` (the `-v` drops the named volume).
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: document first DataLens dashboard

After the connection-setup walkthrough, add the steps to build the
v_bugs dataset and the five charts (status / priority / assignees /
open-vs-closed / new-per-week) plus the dashboard composition. Notes
that the dashboard config persists across down/up but is dropped by
`down -v`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Verification (end-to-end)

After all tasks:

- [ ] **`make test`** — 17 passed (9 unit + 8 integration; 2 new view tests added).
- [ ] **`make psql`** → `SELECT count(*), count(*) FILTER (WHERE is_closed) FROM v_bugs;` returns sane numbers.
- [ ] **`http://localhost:8080`** → Dashboard `Bugs overview` shows 5 charts, all populated.
- [ ] **`make datalens-down && make datalens-up`** — dashboard survives restart (DataLens metadata persisted in named volume).
- [ ] **`git log --oneline -3`** — two new commits on `main` (view migration + README docs), both pushed.

## Rollback

If the dashboards turn out wrong or need a clean slate:

```bash
make datalens-down
docker compose -f docker-compose.yml -f docker-compose.datalens.yml down -v
make datalens-up   # recreates fresh DataLens metadata; reconfigure from scratch
```

The extractor's `bugs` data is in the `pgdata` volume (separate from DataLens's `db-postgres`) and survives this.
