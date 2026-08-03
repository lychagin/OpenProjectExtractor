-- Module ("Модуль", OpenProject customField14) as a first-class column, plus the
-- v_open_bugs view behind the "Открытые баги" DataLens dashboard.
-- Idempotent: safe to run on every container start.

-- ALTER TABLE takes AccessExclusiveLock on `bugs` before even checking whether
-- the column already exists, so the no-op path locks too, held until this
-- migration's transaction commits. Bound the wait so a slow in-flight DataLens
-- query on `bugs` fails the boot fast (container restart policy retries)
-- instead of hanging indefinitely.
SET LOCAL lock_timeout = '10s';

ALTER TABLE bugs ADD COLUMN IF NOT EXISTS module_id   integer;
ALTER TABLE bugs ADD COLUMN IF NOT EXISTS module_name text;

CREATE INDEX IF NOT EXISTS bugs_module_name_idx ON bugs (module_name);

-- Backfill module_id/module_name from the raw snapshot.
--
-- Exposed as a function rather than a bare UPDATE for two reasons:
--   (a) integration tests can call it inside their own transaction — invoking
--       bootstrap_schema() instead would commit, and the db_conn fixture's
--       TRUNCATE with it.
--   (b) it returns the row count, which is what makes idempotence testable.
--
-- The row-wise IS DISTINCT FROM guard means the second and every later run is a
-- no-op, so calling this on every container start costs nothing.
CREATE OR REPLACE FUNCTION backfill_bug_modules()
RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    updated integer;
BEGIN
    UPDATE bugs SET
        module_id   = substring(raw->'_links'->'customField14'->>'href' FROM '([0-9]+)$')::integer,
        module_name = raw->'_links'->'customField14'->>'title'
    WHERE (module_id, module_name) IS DISTINCT FROM (
              substring(raw->'_links'->'customField14'->>'href' FROM '([0-9]+)$')::integer,
              raw->'_links'->'customField14'->>'title');
    GET DIAGNOSTICS updated = ROW_COUNT;
    RETURN updated;
END $$;

SELECT backfill_bug_modules();

-- Feeds the "Открытые баги" DataLens dashboard (one dataset, nine widgets).
--
-- Author and created-date are deliberately NOT filtered here: OpenProject's
-- query 245 hardcodes them, but on the dashboard they are selectors, so the
-- team can widen the default 43-row cut to all 82 open bugs in one click.
--
-- age_days is measured from creation, not from entry into the current status —
-- the latter needs bug_history and is covered by v_bug_time_in_status.
CREATE OR REPLACE VIEW v_open_bugs AS
SELECT
    b.id,
    b.subject,
    b.op_created_at,
    b.status_name,
    b.priority_name,
    b.author_name,
    -- COALESCE, not raw NULLs: 3 open bugs have no module and 6 no assignee, and
    -- DataLens renders NULL as an unlabeled category in bars and selectors.
    COALESCE(b.module_name,   '— без модуля —')  AS module_name,
    COALESCE(b.assignee_name, '— не назначен —') AS assignee_name,
    -- Rank columns exist because DataLens sorts categories alphabetically:
    -- without them the priority order is High/Low/Normal and '8–30 дней'
    -- sorts after '31–90 дней'. Priorities rank by name, not by priority_id —
    -- the id ordering (Low 7 … Immediate 10) is an accident of insertion.
    CASE b.priority_name
        WHEN 'Immediate' THEN 0
        WHEN 'High'      THEN 1
        WHEN 'Normal'    THEN 2
        WHEN 'Low'       THEN 3
        ELSE 9
    END AS priority_rank,
    age.days AS age_days,
    CASE
        WHEN age.days IS NULL  THEN 'неизвестно'
        WHEN age.days <=   7   THEN '0–7 дней'
        WHEN age.days <=  30   THEN '8–30 дней'
        WHEN age.days <=  90   THEN '31–90 дней'
        WHEN age.days <= 180   THEN '91–180 дней'
        ELSE 'больше 180 дней'
    END AS age_bucket,
    CASE
        WHEN age.days IS NULL  THEN 9
        WHEN age.days <=   7   THEN 0
        WHEN age.days <=  30   THEN 1
        WHEN age.days <=  90   THEN 2
        WHEN age.days <= 180   THEN 3
        ELSE 4
    END AS age_bucket_rank
FROM bugs b
CROSS JOIN LATERAL (SELECT (current_date - b.op_created_at::date) AS days) age
WHERE b.deleted_at IS NULL
  AND b.type_name = 'Bug'
  AND NOT COALESCE(is_status_closed(b.status_name), false);

COMMENT ON VIEW v_open_bugs IS
    'Open bugs for the "Открытые баги" dashboard: type=Bug, not soft-deleted, '
    'not in the closed set (Closed / No issue found / Rejected). Author and '
    'created-date filtering is done by dashboard selectors, not here.';
