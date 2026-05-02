# Phase 3 — Bug history trends — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second DataLens dashboard `Bug trends` driven by SQL views over `bug_history`, with three charts (status mix over time, throughput per week, time in status).

**Architecture:** All trend logic lives in a single new SQL migration `003_history_views.sql` (one helper function `is_status_closed`, one core function `bug_state_at`, four views). DataLens reads the views as if they were tables — no Python changes to the extractor itself. Test discipline: each SQL object is preceded by a failing integration test that uses it.

**Tech Stack:** Postgres 16 (functions, views, window functions, `generate_series`), psycopg 3 in tests, DataLens OSS for the UI layer.

**Spec:** `docs/superpowers/specs/2026-05-02-phase-3-history-trends-design.md`

---

## File structure

| File | Action | Purpose |
|---|---|---|
| `db/migrations/003_history_views.sql` | Create | All new SQL: `is_status_closed`, `bug_state_at`, `v_weeks`, `v_bug_status_weekly`, `v_bug_throughput_weekly`, `v_bug_time_in_status`. Also re-creates `v_bugs` to use `is_status_closed` (DRY). |
| `tests/conftest.py` | Modify | Add `make_history_snapshot` fixture for synthetic `bug_history` rows. |
| `tests/test_history_views.py` | Create | 11 integration tests (one per spec test case). |
| `README.md` | Modify | Add "Bug trends dashboard" section after "First dashboard". |

No source code in `src/` changes. No Dockerfile changes (the new migration ships via the existing `COPY db/ /app/db/`), but the extractor image must be rebuilt because the migration files are baked into the image at build time, not mounted at runtime.

---

## Task 1: Scaffold migration file + history-snapshot test fixture

**Files:**
- Create: `db/migrations/003_history_views.sql` (empty placeholder so `bootstrap_schema` finds it)
- Modify: `tests/conftest.py` (add `make_history_snapshot` fixture)
- Test: `tests/test_history_views.py` (one sanity test for the fixture)

- [ ] **Step 1: Create the empty migration file**

```bash
touch db/migrations/003_history_views.sql
```

Add a single header comment so the file isn't truly empty (keeps `psycopg.execute` happy on some configurations):

```sql
-- History trend views consumed by the DataLens "Bug trends" dashboard.
-- Idempotent (CREATE OR REPLACE everywhere).
```

- [ ] **Step 2: Add the `make_history_snapshot` fixture to `tests/conftest.py`**

Append to the end of `tests/conftest.py`:

```python
from datetime import datetime


@pytest.fixture
def make_history_snapshot(db_conn):
    """Insert a synthetic bug + bug_history row at a controlled seen_at and status.

    Use to set up history scenarios for trend-view tests. The bug row is created
    with minimal columns; status_name on the bug row is updated to match the
    latest snapshot status (so v_bugs reflects the same view of the world).
    """
    from psycopg.types.json import Jsonb

    def _insert(bug_id: int, seen_at: datetime, status_name: str, lock_version: int = 1):
        snapshot = {
            "id": bug_id,
            "subject": f"Bug {bug_id}",
            "lockVersion": lock_version,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "_links": {"status": {"href": "/api/v3/statuses/0", "title": status_name}},
        }
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bugs (id, subject, raw, status_name, lock_version) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE "
                "SET status_name = EXCLUDED.status_name, lock_version = EXCLUDED.lock_version",
                (bug_id, f"Bug {bug_id}", Jsonb(snapshot), status_name, lock_version),
            )
            cur.execute(
                "INSERT INTO bug_history (bug_id, lock_version, seen_at, snapshot) "
                "VALUES (%s, %s, %s, %s)",
                (bug_id, lock_version, seen_at, Jsonb(snapshot)),
            )

    return _insert
```

- [ ] **Step 3: Create `tests/test_history_views.py` with one sanity test**

```python
"""Integration tests for db/migrations/003_history_views.sql.

Run with `pytest --integration` (or `make test-integration`).
"""
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


# ISO Mondays (UTC) used as week-bucket anchors throughout the test suite.
W1 = datetime(2026, 4, 27, tzinfo=timezone.utc)  # Monday
W2 = W1 + timedelta(weeks=1)
W3 = W1 + timedelta(weeks=2)
W4 = W1 + timedelta(weeks=3)


def test_make_history_snapshot_creates_bug_and_history_row(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bugs WHERE id = 1")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM bug_history WHERE bug_id = 1")
        assert cur.fetchone()[0] == 1
```

- [ ] **Step 4: Run the sanity test — confirm fixture works**

Run: `make test-integration -- -k test_make_history_snapshot`
(Or: `.venv/bin/python -m pytest tests/test_history_views.py -v --integration -k test_make_history_snapshot`.)

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/conftest.py tests/test_history_views.py
git commit -m "test(phase-3): scaffold history-views migration + history-snapshot fixture"
```

---

## Task 2: `is_status_closed` function + DRY rewrite of `v_bugs`

**Files:**
- Modify: `db/migrations/003_history_views.sql` (add function + recreate `v_bugs`)
- Modify: `tests/test_history_views.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history_views.py`:

```python
def test_is_status_closed_function(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT is_status_closed('Closed')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('No issue found')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('Rejected')")
        assert cur.fetchone()[0] is True
        cur.execute("SELECT is_status_closed('In progress')")
        assert cur.fetchone()[0] is False
        cur.execute("SELECT is_status_closed('Tested')")
        assert cur.fetchone()[0] is False  # narrow definition — Tested is NOT closed
```

- [ ] **Step 2: Run — verify it fails**

Run: `make test-integration -- -k test_is_status_closed_function`

Expected: FAIL with `psycopg.errors.UndefinedFunction: function is_status_closed(unknown) does not exist`.

- [ ] **Step 3: Add the function and rewrite `v_bugs`**

Append to `db/migrations/003_history_views.sql`:

```sql
-- 2.1 Single-source-of-truth for the "closed" definition.
CREATE OR REPLACE FUNCTION is_status_closed(status_name text)
RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT status_name IN ('Closed', 'No issue found', 'Rejected');
$$;

-- 2.2 Re-create v_bugs to delegate is_closed to the function above.
CREATE OR REPLACE VIEW v_bugs AS
SELECT *,
       is_status_closed(status_name) AS is_closed
FROM bugs
WHERE deleted_at IS NULL;

COMMENT ON VIEW v_bugs IS
    'Current bug state for DataLens: hides soft-deletes, adds is_closed via is_status_closed().';
```

- [ ] **Step 4: Run all integration tests — verify the new one passes AND existing v_bugs tests still pass**

Run: `make test-integration`

Expected: all integration tests pass — specifically `test_is_status_closed_function`, `test_v_bugs_view_hides_soft_deleted`, and `test_v_bugs_view_derives_is_closed`.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/test_history_views.py
git commit -m "feat(db): add is_status_closed() and route v_bugs through it"
```

---

## Task 3: `bug_state_at(t)` function

**Files:**
- Modify: `db/migrations/003_history_views.sql`
- Modify: `tests/test_history_views.py`

- [ ] **Step 1: Write three failing tests**

Append to `tests/test_history_views.py`:

```python
def test_bug_state_at_returns_latest_before_t(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="In progress", lock_version=2)
    make_history_snapshot(bug_id=1, seen_at=W3, status_name="Closed", lock_version=3)

    with db_conn.cursor() as cur:
        for t, expected in [(W1, "New"), (W2, "In progress"), (W3, "Closed")]:
            cur.execute("SELECT status_name FROM bug_state_at(%s) WHERE bug_id = 1", (t,))
            assert cur.fetchone()[0] == expected, f"at t={t}"


def test_bug_state_at_excludes_future(db_conn, make_history_snapshot):
    # snapshot exists in the future; querying earlier must return zero rows for this bug
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed")
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bug_state_at(%s)", (W1 - timedelta(seconds=1),))
        assert cur.fetchone()[0] == 0


def test_bug_state_at_empty_history(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM bug_state_at(now())")
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run — verify they fail**

Run: `make test-integration -- -k bug_state_at`

Expected: FAIL with `function bug_state_at(...) does not exist`.

- [ ] **Step 3: Add the function**

Append to `db/migrations/003_history_views.sql`:

```sql
-- 3 State-at-time: for each bug, the most recent snapshot whose seen_at <= t.
CREATE OR REPLACE FUNCTION bug_state_at(t timestamptz)
RETURNS TABLE (bug_id integer, status_name text, is_closed boolean)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (bh.bug_id)
        bh.bug_id,
        (bh.snapshot->'_links'->'status'->>'title')::text,
        is_status_closed((bh.snapshot->'_links'->'status'->>'title')::text)
    FROM bug_history bh
    WHERE bh.seen_at <= t
    ORDER BY bh.bug_id, bh.seen_at DESC;
$$;
```

- [ ] **Step 4: Run — verify they pass**

Run: `make test-integration -- -k bug_state_at`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/test_history_views.py
git commit -m "feat(db): add bug_state_at(t) function over bug_history"
```

---

## Task 4: `v_weeks` + `v_bug_status_weekly`

**Files:**
- Modify: `db/migrations/003_history_views.sql`
- Modify: `tests/test_history_views.py`

- [ ] **Step 1: Write two failing tests**

Append to `tests/test_history_views.py`:

```python
def test_v_bug_status_weekly_groups_correctly(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    make_history_snapshot(bug_id=2, seen_at=W1, status_name="Closed")

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status_name, bug_count FROM v_bug_status_weekly "
            "WHERE week_start = %s ORDER BY status_name",
            (W1,),
        )
        rows = cur.fetchall()
    assert rows == [("Closed", 1), ("New", 1)]


def test_v_bug_status_weekly_handles_transition(db_conn, make_history_snapshot):
    # bug 1 in New on W1, then Closed on W2 — by W2 it must NOT show under 'New'
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT week_start, status_name, bug_count FROM v_bug_status_weekly "
            "WHERE week_start IN (%s, %s) ORDER BY week_start, status_name",
            (W1, W2),
        )
        rows = cur.fetchall()
    assert rows == [(W1, "New", 1), (W2, "Closed", 1)]
```

- [ ] **Step 2: Run — verify they fail**

Run: `make test-integration -- -k v_bug_status_weekly`

Expected: FAIL with `relation "v_bug_status_weekly" does not exist`.

- [ ] **Step 3: Add `v_weeks` and `v_bug_status_weekly`**

Append to `db/migrations/003_history_views.sql`:

```sql
-- 4.1 Weekly grid (Monday 00:00 UTC) from earliest history to now.
CREATE OR REPLACE VIEW v_weeks AS
SELECT generate_series(
    date_trunc('week', (SELECT min(seen_at) FROM bug_history)),
    date_trunc('week', now()),
    interval '1 week'
)::timestamptz AS week_start;

-- 4.2 For each (week, status) pair: how many bugs were in that status as of week_start.
CREATE OR REPLACE VIEW v_bug_status_weekly AS
SELECT w.week_start,
       s.status_name,
       count(*)::int AS bug_count
FROM v_weeks w
CROSS JOIN LATERAL bug_state_at(w.week_start) s
GROUP BY w.week_start, s.status_name;
```

- [ ] **Step 4: Run — verify they pass**

Run: `make test-integration -- -k v_bug_status_weekly`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/test_history_views.py
git commit -m "feat(db): add v_weeks + v_bug_status_weekly for stacked-area chart"
```

---

## Task 5: `v_bug_throughput_weekly`

**Files:**
- Modify: `db/migrations/003_history_views.sql`
- Modify: `tests/test_history_views.py`

- [ ] **Step 1: Write four failing tests**

Append to `tests/test_history_views.py`:

```python
def _throughput_rows(db_conn, week):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, event_count FROM v_bug_throughput_weekly "
            "WHERE week_start = %s ORDER BY event_type",
            (week,),
        )
        return cur.fetchall()


def test_v_bug_throughput_open_count(db_conn, make_history_snapshot):
    # Two bugs created in W1 (only one snapshot each, both 'New').
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    make_history_snapshot(bug_id=2, seen_at=W1, status_name="New")

    rows = _throughput_rows(db_conn, W1)
    assert ("opened", 2) in rows
    assert not any(et == "closed" for et, _ in rows)


def test_v_bug_throughput_close_count_basic(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed", lock_version=2)

    assert ("opened", 1) in _throughput_rows(db_conn, W1)
    assert ("closed", 1) in _throughput_rows(db_conn, W2)


def test_v_bug_throughput_reopen_double_counts(db_conn, make_history_snapshot):
    # New(W1) → Closed(W2) → In progress(W3) → Closed(W4): two close events expected.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New",         lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W2, status_name="Closed",      lock_version=2)
    make_history_snapshot(bug_id=1, seen_at=W3, status_name="In progress", lock_version=3)
    make_history_snapshot(bug_id=1, seen_at=W4, status_name="Closed",      lock_version=4)

    assert ("opened", 1) in _throughput_rows(db_conn, W1)
    assert ("closed", 1) in _throughput_rows(db_conn, W2)
    assert ("closed", 1) in _throughput_rows(db_conn, W4)


def test_v_bug_throughput_initially_closed_counts_as_close(db_conn, make_history_snapshot):
    # Bug exists with a single snapshot already in Closed — must count as both open AND close in W1.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="Closed")
    rows = _throughput_rows(db_conn, W1)
    assert ("opened", 1) in rows
    assert ("closed", 1) in rows
```

- [ ] **Step 2: Run — verify they fail**

Run: `make test-integration -- -k v_bug_throughput`

Expected: FAIL with `relation "v_bug_throughput_weekly" does not exist`.

- [ ] **Step 3: Add `v_bug_throughput_weekly`**

Append to `db/migrations/003_history_views.sql`:

```sql
-- 5 Weekly opens (first snapshot of a bug) and closes (transition into is_closed=true).
-- Reopens count: every close event is independent. Bugs whose first snapshot is already
-- closed produce both an open and a close event in that same week (prev_is_closed IS NULL).
CREATE OR REPLACE VIEW v_bug_throughput_weekly AS
WITH events AS (
    SELECT
        bh.bug_id,
        bh.seen_at,
        is_status_closed((bh.snapshot->'_links'->'status'->>'title')::text) AS is_closed,
        lag(is_status_closed((bh.snapshot->'_links'->'status'->>'title')::text))
            OVER (PARTITION BY bh.bug_id ORDER BY bh.seen_at) AS prev_is_closed,
        row_number() OVER (PARTITION BY bh.bug_id ORDER BY bh.seen_at) AS rn
    FROM bug_history bh
),
opens AS (
    SELECT date_trunc('week', seen_at)::timestamptz AS week_start,
           'opened'::text AS event_type
    FROM events
    WHERE rn = 1
),
closes AS (
    SELECT date_trunc('week', seen_at)::timestamptz AS week_start,
           'closed'::text AS event_type
    FROM events
    WHERE is_closed = true
      AND (prev_is_closed IS NULL OR prev_is_closed = false)
)
SELECT week_start, event_type, count(*)::int AS event_count
FROM (SELECT * FROM opens UNION ALL SELECT * FROM closes) all_events
GROUP BY week_start, event_type;
```

- [ ] **Step 4: Run — verify they pass**

Run: `make test-integration -- -k v_bug_throughput`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/test_history_views.py
git commit -m "feat(db): add v_bug_throughput_weekly for opens/closes line chart"
```

---

## Task 6: `v_bug_time_in_status`

**Files:**
- Modify: `db/migrations/003_history_views.sql`
- Modify: `tests/test_history_views.py`

- [ ] **Step 1: Write two failing tests**

Append to `tests/test_history_views.py`:

```python
def test_v_bug_time_in_status_basic_duration(db_conn, make_history_snapshot):
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New", lock_version=1)
    make_history_snapshot(bug_id=1, seen_at=W1 + timedelta(days=3),
                          status_name="In progress", lock_version=2)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status_name, days_in_status FROM v_bug_time_in_status "
            "WHERE status_name = 'New'"
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "New"
    assert rows[0][1] == pytest.approx(3.0, abs=1e-6)


def test_v_bug_time_in_status_excludes_open_intervals(db_conn, make_history_snapshot):
    # Single snapshot — bug currently in 'New', no transition out yet, must not appear.
    make_history_snapshot(bug_id=1, seen_at=W1, status_name="New")
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM v_bug_time_in_status")
        assert cur.fetchone()[0] == 0
```

- [ ] **Step 2: Run — verify they fail**

Run: `make test-integration -- -k v_bug_time_in_status`

Expected: FAIL with `relation "v_bug_time_in_status" does not exist`.

- [ ] **Step 3: Add `v_bug_time_in_status`**

Append to `db/migrations/003_history_views.sql`:

```sql
-- 6 Per-bug, per-status interval durations (in days). Open intervals (last snapshot per
-- bug — bug still in that status) are excluded: no end timestamp is known.
-- DataLens aggregates this as avg(days_in_status) GROUP BY status_name.
CREATE OR REPLACE VIEW v_bug_time_in_status AS
WITH transitions AS (
    SELECT bh.bug_id,
           bh.seen_at,
           (bh.snapshot->'_links'->'status'->>'title')::text AS status_name,
           lead(bh.seen_at) OVER (PARTITION BY bh.bug_id ORDER BY bh.seen_at) AS next_seen_at
    FROM bug_history bh
)
SELECT status_name,
       extract(epoch FROM (next_seen_at - seen_at)) / 86400.0 AS days_in_status
FROM transitions
WHERE next_seen_at IS NOT NULL;
```

- [ ] **Step 4: Run — verify all 11 trend tests pass**

Run: `make test-integration`

Expected: all integration tests pass — including the 11 new ones in `test_history_views.py`. Old `test_db.py` tests still pass too (sanity check that the v_bugs rewrite didn't regress anything).

- [ ] **Step 5: Commit**

```bash
git add db/migrations/003_history_views.sql tests/test_history_views.py
git commit -m "feat(db): add v_bug_time_in_status for status-duration bar chart"
```

---

## Task 7: Rebuild image, deploy migration to running Postgres, verify in psql

The migration file is COPYed into the extractor image at build time (see `Dockerfile: COPY db/ /app/db/`). The container reads it on startup via `bootstrap_schema`. This task rebuilds the image and verifies the migration applies cleanly to the running stack.

**Files:** none (operational task only).

- [ ] **Step 1: Rebuild the extractor image**

```bash
docker compose build extractor
```

Expected: build succeeds. The new `003_history_views.sql` is now in the image.

- [ ] **Step 2: Bring the stack up (light: just postgres + extractor)**

```bash
make up
```

If it was already up, force a fresh extractor:

```bash
docker compose up -d --force-recreate extractor
```

- [ ] **Step 3: Watch logs to confirm migration applied**

```bash
docker compose logs --tail=30 extractor
```

Expected: line `applied migration: 003_history_views.sql` appears once. No error tracebacks.

- [ ] **Step 4: Spot-check in psql**

```bash
make psql
```

Then in the psql prompt, run each of these and verify it returns rows (or zero rows without error — both are fine; we're checking that the SQL objects exist):

```sql
SELECT is_status_closed('Closed');                      -- expect: t
SELECT count(*) FROM bug_state_at(now());               -- expect: same as count(*) from v_bugs
SELECT count(*) FROM v_weeks;                           -- expect: at least 1
SELECT * FROM v_bug_status_weekly LIMIT 3;
SELECT * FROM v_bug_throughput_weekly LIMIT 3;
SELECT * FROM v_bug_time_in_status LIMIT 3;
\q
```

If any object is missing, the migration didn't apply — go back and check container logs.

- [ ] **Step 5: No commit (operational task)**

---

## Task 8: Build the `Bug trends` dashboard in DataLens

Manual UI walkthrough. Mirrors the existing "First dashboard" workflow in the README. No file changes — DataLens persists everything in `datalens-postgres` (`pg-us-db` named volume).

**Files:** none (the README update lives in Task 9).

- [ ] **Step 1: Bring the full stack up**

```bash
make datalens-up
```

If you switched from `make up` → `make datalens-up`, watch for the network-alias gotcha already documented in README.md (the `--force-recreate postgres extractor` workaround).

- [ ] **Step 2: Open DataLens UI**

Navigate to <http://localhost:8080>, log in as `admin` / `admin`. The connection `extractor-bugs` from Phase 2b is already there.

- [ ] **Step 3: Create three datasets**

For each row in this table: *Datasets → + New → connection `extractor-bugs` → drag the source view onto the canvas → save with the given name*.

| Dataset name | Source view |
|---|---|
| `bug_status_weekly` | `v_bug_status_weekly` |
| `bug_throughput_weekly` | `v_bug_throughput_weekly` |
| `bug_time_in_status` | `v_bug_time_in_status` |

DataLens will auto-detect field types. Confirm the measure column is recognised as a measure (numeric); if not, change it via the field's "Field type" dropdown.

- [ ] **Step 4: Create chart "Bug status mix over time"**

*Charts → + New → dataset `bug_status_weekly` → chart type "Stacked area"*:
- X: `week_start`
- Y: `bug_count` (sum)
- Colors: `status_name`
- Stack ordering: drag closed statuses (`Closed`, `No issue found`, `Rejected`) to the bottom of the legend so they pin under the active statuses.
- Save as `Bug status mix over time`.

- [ ] **Step 5: Create chart "Bug throughput per week"**

*Charts → + New → dataset `bug_throughput_weekly` → chart type "Line"*:
- X: `week_start`
- Y: `event_count` (sum)
- Colors: `event_type` (gives two lines: opened, closed)
- Save as `Bug throughput per week`.

- [ ] **Step 6: Create chart "Average time in status"**

*Charts → + New → dataset `bug_time_in_status` → chart type "Bar (horizontal)" (Линейчатая диаграмма)*:
- Y: `status_name`
- X: `days_in_status` (avg)
- Sort: by X descending.
- Measure filter: `count([days_in_status]) > 5` (require ≥5 closed transitions through that status — DataLens OSS top-N workaround, same as `Bug load by assignee`).
- Save as `Average time in status`.

- [ ] **Step 7: Create the dashboard**

*Dashboards → + New → name `Bug trends`*. Layout (12-column grid):

```
Row 1, full width (12 cols):     Bug status mix over time
Row 2, full width (12 cols):     Bug throughput per week
Row 3, 8 cols:                   Average time in status
Row 3, 4 cols (text widget):     "Closed = Closed / No issue found /
                                  Rejected. Reopens count as new
                                  transitions. History from 2026-04-30."
```

Save the dashboard. Verify each chart renders without errors — even on a near-empty `bug_history`, the SQL is correct and you should see at least one week-bucket appear once you've been collecting for a few hours.

- [ ] **Step 8: No commit (DataLens persists in its own Postgres, not in git)**

---

## Task 9: README update + final commit + push

**Files:**
- Modify: `README.md` (add a "Bug trends dashboard" section after the existing "First dashboard")

- [ ] **Step 1: Add a new section to README.md**

Insert the following block after the "First dashboard" section (which ends with the line about `down -v` resetting). Place it before the "## Tests" heading.

```markdown
### Bug trends dashboard

A second dashboard, fed by SQL views over `bug_history`. Build it after `Bugs overview` is up.

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
```

- [ ] **Step 2: Verify the new section renders correctly**

```bash
git diff README.md
```

Eye-check formatting (markdown headings, code-fence closures, list indentation).

- [ ] **Step 3: Final integration-test run + lint**

```bash
make test-integration
```

Expected: all tests pass (existing ones + the 11 new ones in `test_history_views.py` + the fixture sanity test).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: Bug trends dashboard walkthrough"
```

- [ ] **Step 5: Push**

```bash
git push
```

Expected: push succeeds; `main` matches `origin/main`.

---

## Verification (end-to-end)

1. `make test-integration` — 11 new tests pass + all prior tests still pass.
2. `make psql` — `\d v_bug_status_weekly` shows the view; `SELECT * FROM v_bug_throughput_weekly LIMIT 5` works.
3. <http://localhost:8080> — `Bug trends` dashboard exists, all three charts render (even if sparse).
4. `git log --oneline origin/main..HEAD` is empty (everything pushed).

## Non-goals (explicit reminder)

This plan deliberately does NOT:
- Add backfill from OpenProject's `/activities` feed (Phase 3.3 in the roadmap — out of scope).
- Add materialized views or a refresh cron (only if performance becomes a real problem, see deferred follow-ups in the spec).
- Add per-assignee or per-priority drill-downs.
- Replace the existing `Bugs overview` dashboard.

## Deferred follow-ups (post-merge)

Document these in your own task tracker — they're not part of this plan, but worth revisiting:
- 2–4 weeks after deploy: sanity-check `bug_history` row count and reopen counts on the live data.
- If any chart query exceeds ~1s on real data: add `INDEX bug_history(seen_at)` or migrate `v_bug_status_weekly` to a materialized view refreshed daily.
- `Average time in status` is downward-biased while many bugs are still open (open intervals are excluded from the view). The dashboard text block flags this; revisit the wording if confusing.
