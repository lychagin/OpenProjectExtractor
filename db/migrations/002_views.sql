-- View consumed by DataLens datasets.
-- Idempotent (CREATE OR REPLACE); runs on every container start.

-- v_bugs is `SELECT *`, so its column list is only as wide as `bugs` was the
-- moment the view was (re)created. Any later ALTER TABLE bugs ADD COLUMN
-- shifts is_closed out of its existing position and CREATE OR REPLACE VIEW
-- refuses that ("cannot change name of view column"). DROP + CREATE has no
-- such restriction, so the view just re-tracks the table's current shape on
-- every boot, with no maintenance needed here when bugs gains columns.
DROP VIEW IF EXISTS v_bugs;
CREATE OR REPLACE VIEW v_bugs AS
SELECT
    *,
    status_name IN ('Closed', 'No issue found', 'Rejected') AS is_closed
FROM bugs
WHERE deleted_at IS NULL;

COMMENT ON VIEW v_bugs IS
    'Current bug state for DataLens: hides soft-deletes, adds is_closed (per OpenProject isClosed flag: Closed / No issue found / Rejected).';
