-- Module ("Модуль", OpenProject customField14) as a first-class column, plus the
-- v_open_bugs view behind the "Открытые баги" DataLens dashboard.
-- Idempotent: safe to run on every container start.

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
        module_id   = NULLIF(regexp_replace(
                          raw->'_links'->'customField14'->>'href', '^.*/', ''), '')::integer,
        module_name = raw->'_links'->'customField14'->>'title'
    WHERE (module_id, module_name) IS DISTINCT FROM (
              NULLIF(regexp_replace(
                  raw->'_links'->'customField14'->>'href', '^.*/', ''), '')::integer,
              raw->'_links'->'customField14'->>'title');
    GET DIAGNOSTICS updated = ROW_COUNT;
    RETURN updated;
END $$;

SELECT backfill_bug_modules();
