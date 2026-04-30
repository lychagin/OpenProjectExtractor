-- Initial schema for OpenProject bug extractor.
-- Idempotent: safe to run on every container start.

CREATE TABLE IF NOT EXISTS bugs (
    id                   integer       PRIMARY KEY,

    -- Scalars copied from the work_package JSON.
    subject              text          NOT NULL,
    description_md       text,
    start_date           date,
    due_date             date,
    op_created_at        timestamptz,
    op_updated_at        timestamptz,
    lock_version         integer,
    percentage_done      integer,
    estimated_time       text,
    spent_time           text,
    story_points         integer,

    -- Denormalized references (id + human name) extracted from _links.
    status_id            integer,
    status_name          text,
    priority_id          integer,
    priority_name        text,
    type_id              integer,
    type_name            text,
    project_id           integer,
    project_name         text,
    author_id            integer,
    author_name          text,
    assignee_id          integer,
    assignee_name        text,
    responsible_id       integer,
    responsible_name     text,
    version_id           integer,
    version_name         text,

    -- Full work_package response — insurance against schema drift.
    raw                  jsonb         NOT NULL,

    -- Bookkeeping.
    synced_at            timestamptz   NOT NULL DEFAULT now(),
    deleted_at           timestamptz
);

CREATE INDEX IF NOT EXISTS bugs_op_updated_at_idx ON bugs (op_updated_at);
CREATE INDEX IF NOT EXISTS bugs_status_name_idx   ON bugs (status_name);
CREATE INDEX IF NOT EXISTS bugs_assignee_id_idx   ON bugs (assignee_id);
CREATE INDEX IF NOT EXISTS bugs_deleted_at_idx    ON bugs (deleted_at);


CREATE TABLE IF NOT EXISTS bug_history (
    id            bigserial     PRIMARY KEY,
    bug_id        integer       NOT NULL REFERENCES bugs (id) ON DELETE CASCADE,
    lock_version  integer       NOT NULL,
    seen_at       timestamptz   NOT NULL DEFAULT now(),
    snapshot      jsonb         NOT NULL
);

CREATE INDEX IF NOT EXISTS bug_history_bug_id_seen_at_idx
    ON bug_history (bug_id, seen_at DESC);
