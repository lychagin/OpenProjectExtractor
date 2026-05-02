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
