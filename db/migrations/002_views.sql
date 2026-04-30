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
