-- View consumed by DataLens datasets.
-- Idempotent (CREATE OR REPLACE); runs on every container start.

-- v_bugs used to be defined here with the closed-set inlined as a literal.
-- It is now defined exactly once, in 003_history_views.sql, which expresses
-- the closed-set via is_status_closed() instead of re-inlining it — keeping
-- a single source of truth for "closed" across v_bugs and the history views.
