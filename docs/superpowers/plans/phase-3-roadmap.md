# Phase 3 Roadmap — Trends from `bug_history`

> Sketch, not an execution plan. Promote to a `/writing-plans` doc when we're ready to start.

## Goal

Build dashboards that show how the bug pool changes **over time**, not just its current snapshot. Three charts worth chasing:

1. **Status mix over time** — stacked area: for every week, how many bugs were in each status (`New`, `In progress`, `Closed`, …). Lets you see backlogs growing or draining.
2. **Throughput per week** — line: bugs *opened* vs bugs *closed* in each week (created and closed defined by snapshot transitions). The classic "are we keeping up?" chart.
3. **Average time-in-status** — bar: for each status, average duration a bug spent there before transitioning out. Bottleneck finder.

## Data we have

`bug_history(bug_id, lock_version, seen_at, snapshot jsonb)` — append-only. One row per detected `lock_version` change. Initial sync inserted one row per bug with the state at that moment.

What this means in practice:
- **Snapshots only exist from the day the extractor first ran** (≈ 2026-04-30). Trends before that date can't be reconstructed — we have no information.
- **Between two snapshots, the state is implicit:** the bug was in whatever the prior snapshot says, until the next snapshot updates it.
- **`snapshot.createdAt`** inside the JSON gives the bug's creation date, which is older than our first snapshot. So "bugs created per week" works back to the project's beginning, but "bugs closed per week" only works from when we started watching.

## Key SQL piece: state-at-time view

The hard part is "what was each bug's status on Monday week 17?" Approach: a SQL function or view that joins each bug to the latest history row with `seen_at <= target_date`.

```sql
-- Sketch — likely lives in db/migrations/003_history_views.sql
CREATE OR REPLACE FUNCTION bug_state_at(t timestamptz)
RETURNS TABLE (
    bug_id integer,
    status_name text,
    is_closed boolean
)
LANGUAGE sql STABLE AS $$
    SELECT DISTINCT ON (bh.bug_id)
        bh.bug_id,
        (bh.snapshot->'_links'->'status'->>'title')::text AS status_name,
        (bh.snapshot->'_links'->'status'->>'title') IN ('Closed', 'No issue found', 'Rejected') AS is_closed
    FROM bug_history bh
    WHERE bh.seen_at <= t
    ORDER BY bh.bug_id, bh.seen_at DESC;
$$;
```

DataLens can then drive a chart by sampling this function at, say, 52 weekly points. (DataLens doesn't natively call functions, but a parameterized view + a "weeks" generator series would do it.)

Alternative: pre-compute a wide `v_bug_status_weekly` table maintained by a small SQL CTE that runs nightly. Heavier but simpler for DataLens to consume.

## Tasks (rough)

- **3.1** — Decide between on-demand function vs pre-computed weekly snapshot table. Tradeoff: query speed vs schema simplicity. Recommendation: pre-computed if Phase 3 charts feel slow.
- **3.2** — Migration `db/migrations/003_history_views.sql` with the chosen approach. Integration tests: state-at-time on synthetic data with known transitions.
- **3.3** — Backfill consideration: do we accept "trends start from extractor first run" as a hard limit, or pull historical activity from OpenProject's own activity feed (`/api/v3/work_packages/<id>/activities`)? The activity feed has every status change with timestamps — could populate `bug_history` retroactively for a fuller history. Big task on its own.
- **3.4** — DataLens dataset on the new view + 3 charts (stacked area, throughput line, time-in-status bar).
- **3.5** — Add charts to `Bugs overview` dashboard or create separate `Bug trends` dashboard.

## Open questions to settle before starting

- **Point in time vs interval.** "Bugs in 'In progress' on Monday" is unambiguous (point in time). "Bugs *closed during* week 17" requires defining the window's open/close edges. Pin down conventions first.
- **Reopen detection.** A bug going `Closed → In progress` should bump `lock_version` and produce a new snapshot. Check: does OpenProject actually allow this transition, and does the API expose it the same way? Verify on a real reopened bug before designing the throughput chart.
- **Backfill yes/no.** Without backfill, the next 3-6 months of dashboards will look thin. Some users will tolerate that, others won't. Strong signal from stakeholders before committing.
