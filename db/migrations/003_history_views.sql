-- History trend views consumed by the DataLens "Bug trends" dashboard.
-- Idempotent (CREATE OR REPLACE everywhere).

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

-- 4.1 Weekly grid (Monday 00:00 UTC).
-- Upper bound: GREATEST(now, max(seen_at)) rather than plain now() for two reasons:
--   (a) integration-test fixtures use forward-dated synthetic timestamps (W2-W4 in 2026-05).
--   (b) any seen_at slightly ahead of wall-clock (clock skew, bulk import) is handled
--       gracefully: an extra week appears with the bugs' current status, which is correct.
CREATE OR REPLACE VIEW v_weeks AS
SELECT generate_series(
    date_trunc('week', (SELECT min(seen_at) FROM bug_history)),
    GREATEST(
        date_trunc('week', now()),
        date_trunc('week', (SELECT max(seen_at) FROM bug_history))
    ),
    interval '1 week'
)::timestamptz AS week_start;

-- 4.2 For each (week, status) pair: how many bugs were in that status as of week_start.
CREATE OR REPLACE VIEW v_bug_status_weekly AS
SELECT w.week_start,
       s.status_name,
       count(*)::int AS bug_count   -- bigint -> int4; safe: no bucket will hold >2B bugs
FROM v_weeks w
CROSS JOIN LATERAL bug_state_at(w.week_start) s
GROUP BY w.week_start, s.status_name;
