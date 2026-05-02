# Phase 3 — Bug history trends (design)

Status: design approved 2026-05-02. Ready for `/writing-plans`.

## Goal

Add a second DataLens dashboard `Bug trends` driven by `bug_history` snapshots, with three charts:

1. **Bug status mix over time** — stacked area, weekly snapshots of how many bugs were in each status.
2. **Bug throughput per week** — line, two series (opened / closed) per ISO week.
3. **Average time in status** — horizontal bar, mean duration each status holds a bug before transitioning out.

History grows forward from when the extractor first ran (~2026-04-30). No backfill from OpenProject's activity feed.

## Decisions (locked)

| # | Decision | Chosen |
|---|---|---|
| 1 | History depth | Grow forward only, no backfill |
| 2 | Timing | Start now (charts will be thin for ~4 weeks) |
| 3 | State-at-time mechanism | On-demand SQL function (not pre-computed table, not materialized view) |
| 4 | Time-bucket convention | ISO week, UTC, point sampled at week start (Monday 00:00 UTC) |
| 5 | Reopen handling | Counted as normal transitions (every close adds +1 to that week's `closed`) |
| 6 | Closed-set definition | Unchanged — `Closed`, `No issue found`, `Rejected` (extracted into a SQL function for DRY) |
| 7 | MVP scope | All three charts |
| 8 | Dashboard placement | New `Bug trends` dashboard, separate from `Bugs overview` |

## Architecture

```
bug_history (append-only, jsonb snapshots, seen_at)
       │
       ▼
SQL function: bug_state_at(t timestamptz)        ─┐
   DISTINCT ON (bug_id) WHERE seen_at <= t        │  003_history_views.sql
                                                  │
SQL view: v_weeks                                 │
   generate_series(min(seen_at), now(), '1 week') │
                                                  │
SQL view: v_bug_status_weekly      ──▶ Chart 1   │
SQL view: v_bug_throughput_weekly  ──▶ Chart 2   │
SQL view: v_bug_time_in_status     ──▶ Chart 3  ─┘
       │
       ▼
DataLens datasets (3) → Charts (3) → Dashboard `Bug trends`
```

No new moving parts on the extractor side. Everything sits read-only on top of the existing `bug_history` table. DataLens consumes the views as if they were tables.

## SQL surface (`db/migrations/003_history_views.sql`)

### Helper: closed-set as a function

```sql
CREATE OR REPLACE FUNCTION is_status_closed(status_name text)
RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT status_name IN ('Closed', 'No issue found', 'Rejected');
$$;
```

`v_bugs.is_closed` is rewritten to call this function instead of inlining the IN-list (semantic no-op, single source of truth).

### State-at-time function

```sql
CREATE OR REPLACE FUNCTION bug_state_at(t timestamptz)
RETURNS TABLE (bug_id integer, status_name text, is_closed boolean)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (bh.bug_id)
        bh.bug_id,
        (bh.snapshot->'_links'->'status'->>'title')::text AS status_name,
        is_status_closed((bh.snapshot->'_links'->'status'->>'title')::text)
    FROM bug_history bh
    WHERE bh.seen_at <= t
    ORDER BY bh.bug_id, bh.seen_at DESC;
$$;
```

Relies on `bug_history(bug_id, seen_at DESC)` index (already exists from migration 001).

### `v_weeks`

```sql
CREATE OR REPLACE VIEW v_weeks AS
SELECT generate_series(
    date_trunc('week', (SELECT min(seen_at) FROM bug_history)),
    date_trunc('week', now()),
    interval '1 week'
)::timestamptz AS week_start;
```

Empty when `bug_history` is empty (safe). All three weekly views derive their X axis from this view.

### `v_bug_status_weekly`

```sql
CREATE OR REPLACE VIEW v_bug_status_weekly AS
SELECT w.week_start, s.status_name, count(*)::int AS bug_count
FROM v_weeks w
CROSS JOIN LATERAL bug_state_at(w.week_start) s
GROUP BY w.week_start, s.status_name;
```

### `v_bug_throughput_weekly`

Long form, one row per `(week_start, event_type, event_count)`:

- `event_type='opened'` — count of bugs whose first `bug_history` row lands in `[week_start, week_start + 7d)`.
- `event_type='closed'` — count of snapshots in the same window where `is_closed=true` AND either there was no prior snapshot for the bug or the prior snapshot had `is_closed=false`. Reopens add subsequent +1's.

Implementation: a CTE with `LAG(is_closed) OVER (PARTITION BY bug_id ORDER BY seen_at)`, two `UNION ALL` legs (opens / closes), then weekly aggregation.

### `v_bug_time_in_status`

```sql
CREATE OR REPLACE VIEW v_bug_time_in_status AS
WITH transitions AS (
    SELECT bug_id, seen_at,
           snapshot->'_links'->'status'->>'title' AS status_name,
           lead(seen_at) OVER (PARTITION BY bug_id ORDER BY seen_at) AS next_seen_at
    FROM bug_history
)
SELECT status_name,
       extract(epoch FROM (next_seen_at - seen_at)) / 86400 AS days_in_status
FROM transitions
WHERE next_seen_at IS NOT NULL;
```

Open intervals (last snapshot per bug — bug currently in that status) are excluded. DataLens computes `avg(days_in_status) GROUP BY status_name`.

## Tests (`tests/test_history_views.py`)

All integration, on the existing `db_conn` fixture (transaction-rolled-back, safe against production data).

**Helper fixture in `tests/conftest.py`:**

```python
@pytest.fixture
def make_history_snapshot(db_conn):
    """Insert a synthetic bug + bug_history row at a controlled seen_at + status."""
```

**Cases (one assertion idea each):**

1. `test_bug_state_at_returns_latest_before_t`
2. `test_bug_state_at_excludes_future`
3. `test_bug_state_at_empty_history`
4. `test_v_bug_status_weekly_groups_correctly`
5. `test_v_bug_status_weekly_handles_transition`
6. `test_v_bug_throughput_open_count`
7. `test_v_bug_throughput_close_count_basic`
8. `test_v_bug_throughput_reopen_double_counts` — reopens add to throughput
9. `test_v_bug_throughput_initially_closed_counts_as_close` — single snapshot already in Closed
10. `test_v_bug_time_in_status_basic_duration`
11. `test_v_bug_time_in_status_excludes_open_intervals`

Estimated size: ~150 lines of test + ~40 lines of helper fixture.

## DataLens layer

### Datasets (3 new)

| Dataset name | Source view | Fields |
|---|---|---|
| `bug_status_weekly` | `v_bug_status_weekly` | `week_start` (date), `status_name` (string dim), `bug_count` (int measure) |
| `bug_throughput_weekly` | `v_bug_throughput_weekly` | `week_start`, `event_type` (`'opened'`/`'closed'`), `event_count` |
| `bug_time_in_status` | `v_bug_time_in_status` | `status_name` (string dim), `days_in_status` (float measure) |

`bug_throughput_weekly` is intentionally long-form so DataLens line charts get two series via the `event_type` color field — no calculated columns needed.

### Charts

| Chart | Type | X | Y | Color | Notes |
|---|---|---|---|---|---|
| Bug status mix over time | Stacked area | `week_start` | sum(`bug_count`) | `status_name` | closed statuses pinned to the bottom of the stack so changing/active statuses ride on top — easier to read week-to-week deltas |
| Bug throughput per week | Line (2 series) | `week_start` | sum(`event_count`) | `event_type` | |
| Average time in status | Horizontal bar | avg(`days_in_status`) | `status_name` | — | measure filter `count(days_in_status) > 5` (i.e. require at least 5 closed transitions through that status) to suppress thinly-populated bars; sort by X desc |

### Dashboard `Bug trends`

```
┌──────────────────────────────────────────────────┐
│ Bug status mix over time          [12 cols]      │  Row 1
├──────────────────────────────────────────────────┤
│ Bug throughput per week           [12 cols]      │  Row 2
├────────────────────────────┬─────────────────────┤
│ Average time in status     │  Text block:        │  Row 3
│ [8 cols]                   │  "Closed = …;       │
│                            │   Reopens counted;  │
│                            │   History from      │
│                            │   2026-04-30"       │
│                            │  [4 cols]           │
└────────────────────────────┴─────────────────────┘
```

Persists in `datalens-postgres` (`pg-us-db` volume), survives `make datalens-down/up`.

### README update

New section `Bug trends dashboard` next to the existing "First dashboard" walkthrough, mirroring its structure: dataset creation steps → chart configuration → dashboard layout.

## Non-goals

- No backfill from OpenProject's `/api/v3/work_packages/<id>/activities` feed. Pre-extractor history is irreversibly missing.
- No materialized view / pre-computed snapshot table. On-demand function is enough for ~760 bugs × 52 weeks/year.
- No cron-driven `REFRESH MATERIALIZED VIEW`.
- No top-N filter on `Average time in status`. DataLens OSS doesn't have one; the `count(*) > 5` measure filter is the workaround (same approach we used for `Bug load by assignee`).
- No per-assignee or per-priority throughput. Single dimension (`event_type`) only.
- No drill-through from chart to bug list.
- Dashboard does NOT replace `Bugs overview`. The two are complementary (snapshot now vs trends over time).

## Deferred follow-ups

- **2–4 weeks after deploy**: check `bug_history` row count and `v_bug_throughput_weekly` shape. If charts look unhealthy (e.g., implausible reopen counts → reopens may need different handling), revisit.
- **If query performance degrades** (>1s for any chart): add `INDEX bug_history(seen_at)` or migrate `v_bug_status_weekly` to a materialized view refreshed daily.
- **Time-in-status bias**: while many bugs are still open, average duration is downward-biased (open intervals excluded). Document this caveat in the dashboard's text block; revisit if it becomes misleading.

## Implementation order (rough — refined in `/writing-plans`)

1. Migration `003_history_views.sql` (function + 4 views) + `v_bugs` rewrite.
2. Test helper fixture + 11 integration tests.
3. Rebuild extractor image so `db/migrations/003_*.sql` ships into the container.
4. Verify in psql: `bug_state_at(now())` returns same row count as `v_bugs`.
5. Bring DataLens up. Create 3 datasets, 3 charts, dashboard.
6. README section.
7. Commit + push.
