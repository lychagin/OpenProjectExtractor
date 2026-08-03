# Open bugs dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a third DataLens dashboard, `Открытые баги`, reproducing OpenProject query 245 (Bug-only, our closed-set) with author/date as dashboard selectors and `Модуль` as a first-class column.

**Architecture:** One new idempotent migration adds `module_id` / `module_name` to `bugs`, backfills them from the `raw` jsonb via a callable SQL function, and creates the `v_open_bugs` view. `src/db.py` gains the two columns so every sync cycle keeps them fresh. DataLens consumes `v_open_bugs` as a single dataset.

**Tech Stack:** Python 3.12 + psycopg 3, Postgres 16, pytest, Docker Compose, DataLens OSS.

**Spec:** `docs/superpowers/specs/2026-08-03-open-bugs-dashboard-design.md`

## Global Constraints

- Migrations live in `db/migrations/`, are applied in lexicographic order by `db.bootstrap_schema()` on **every** container start, and must therefore be **idempotent** (`IF NOT EXISTS`, `CREATE OR REPLACE`, guarded `UPDATE`).
- `bootstrap_schema()` calls `conn.commit()`. Tests must never invoke it to exercise DML — the `db_conn` fixture's `TRUNCATE bugs` would be committed against real data. Backfill logic is therefore exposed as a SQL function the tests can call inside their own transaction.
- Integration tests are opt-in via `--integration` and run inside a transaction rolled back at teardown (`tests/conftest.py`). This is what makes them safe against a populated DB.
- The closed-set is `Closed`, `No issue found`, `Rejected`, and is only ever expressed through the existing `is_status_closed(text)` function. Never re-inline the literal list.
- Russian display strings are user-facing and must be copied **verbatim**: `'— без модуля —'`, `'— не назначен —'`, `'0–7 дней'`, `'8–30 дней'`, `'31–90 дней'`, `'91–180 дней'`, `'больше 180 дней'`, `'неизвестно'`. Note the en-dash `–` inside bucket labels and the em-dash `—` inside placeholders — they are different characters.
- OpenProject custom field for `Модуль` is `customField14` (schema `13-7`). Priorities are `Low(7) < Normal(8) < High(9) < Immediate(10)`; ordering is by **name**, not id.
- Commit messages: conventional commits, ending with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Before you start

Integration tests need a Postgres. There is none running locally (no `pgdata` volume, no images):

```bash
make up          # postgres + extractor; extractor begins syncing from OpenProject
docker compose logs -f extractor   # wait for the first "cycle" line, then Ctrl-C
```

`make test` (unit, no DB) works without this.

## File Structure

| File | Responsibility |
|---|---|
| `db/migrations/004_module_and_open_bugs.sql` | *Create.* Module columns, `backfill_bug_modules()`, `v_open_bugs`. |
| `src/db.py` | *Modify.* `MODULE_CF_KEY` constant; `module_id`/`module_name` in `_COLUMNS` and `_wp_to_row`. |
| `tests/test_wp_row.py` | *Create.* Pure unit tests for the `_wp_to_row` module mapping. Runs in `make test`. |
| `tests/test_open_bugs_view.py` | *Create.* Integration tests for the backfill function and `v_open_bugs`. |
| `README.md` | *Modify.* New "Open bugs dashboard" section with the DataLens build checklist. |

Why a separate `tests/test_wp_row.py` rather than adding to `tests/test_db.py`: that file carries a module-level `pytestmark = pytest.mark.integration`, so anything added there is skipped without `--integration`. The `_wp_to_row` mapping is pure and deserves coverage in the fast unit run.

---

### Task 1: Module columns + backfill function

**Files:**
- Create: `db/migrations/004_module_and_open_bugs.sql`
- Test: `tests/test_open_bugs_view.py`

**Interfaces:**
- Consumes: `bugs` table and its `raw jsonb` column (from `001_init.sql`).
- Produces: columns `bugs.module_id integer`, `bugs.module_name text`; SQL function `backfill_bug_modules() RETURNS integer` (returns the number of rows it updated).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_open_bugs_view.py`:

```python
"""Integration tests for migration 004 — module columns, backfill, v_open_bugs.

Run with `pytest --integration` (or `make test-integration`). Every mutation
here is rolled back by the db_conn fixture, so this is safe against a
populated database.
"""
import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


def _insert(
    conn,
    bug_id,
    *,
    module=("/api/v3/custom_options/64", "Терра - Пользователи"),
    status="In progress",
    type_name="Bug",
    priority="Normal",
    assignee="Исполнитель",
    author="Автор",
    age_days=10,
    deleted=False,
):
    """Insert a bug row directly, leaving module_id/module_name unset.

    The module columns are deliberately NOT written here — that is what
    backfill_bug_modules() is expected to fill in from raw.
    """
    links = {
        "status": {"href": "/api/v3/statuses/7", "title": status},
        "type": {"href": "/api/v3/types/7", "title": type_name},
        "priority": {"href": "/api/v3/priorities/8", "title": priority},
    }
    if module is not None:
        links["customField14"] = {"href": module[0], "title": module[1]}
    raw = {"id": bug_id, "subject": f"Bug {bug_id}", "_links": links}

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bugs (id, subject, raw, type_name, status_name, priority_name,"
            "                  assignee_name, author_name, op_created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
            "         now() - make_interval(days => %s))",
            (
                bug_id, f"Bug {bug_id}", Jsonb(raw), type_name, status, priority,
                assignee, author, age_days,
            ),
        )
        if deleted:
            cur.execute("UPDATE bugs SET deleted_at = now() WHERE id = %s", (bug_id,))


def test_backfill_populates_module_from_raw(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (64, "Терра - Пользователи")


def test_backfill_is_idempotent(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        # Second run must find nothing left to do.
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 0


def test_backfill_leaves_null_when_custom_field_absent(db_conn):
    _insert(db_conn, 1, module=None)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (None, None)


def test_backfill_repairs_a_stale_module(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("UPDATE bugs SET module_name = 'Старое значение' WHERE id = 1")
        cur.execute("SELECT backfill_bug_modules()")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT module_name FROM bugs WHERE id = 1")
        assert cur.fetchone()[0] == "Терра - Пользователи"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_open_bugs_view.py -v --integration`
Expected: 4 FAILED with `psycopg.errors.UndefinedFunction: function backfill_bug_modules() does not exist`

- [ ] **Step 3: Write the migration**

Create `db/migrations/004_module_and_open_bugs.sql`:

```sql
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_open_bugs_view.py -v --integration`
Expected: 4 PASSED

- [ ] **Step 5: Confirm nothing else regressed**

Run: `make test-integration`
Expected: 37 passed (33 existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add db/migrations/004_module_and_open_bugs.sql tests/test_open_bugs_view.py
git commit -m "$(cat <<'EOF'
feat(db): denormalize Модуль (customField14) into bugs.module_id/module_name

Backfilled from the raw snapshot, so no re-fetch from OpenProject is needed —
raw already carries _links.customField14 for all 1220 bugs.

The backfill is a callable function rather than a bare UPDATE: integration
tests can invoke it inside their own rolled-back transaction, and its row
count makes idempotence directly testable.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Keep the module columns fresh on every sync

**Files:**
- Modify: `src/db.py:52-97` (`_COLUMNS` tuple and `_wp_to_row`)
- Test: `tests/test_wp_row.py` (create), `tests/test_db.py` (extend)

**Interfaces:**
- Consumes: `bugs.module_id` / `bugs.module_name` from Task 1; existing `_link_id()` / `_link_title()` helpers in `src/db.py:36-49`.
- Produces: `db.MODULE_CF_KEY: str` (value `"customField14"`); `_wp_to_row()` output gains keys `module_id: int | None` and `module_name: str | None`.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_wp_row.py`:

```python
"""Unit tests for the work-package → DB row mapping. No database required."""
import db


def _wp(links=None):
    return {
        "id": 1,
        "subject": "Bug 1",
        "lockVersion": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "_links": links or {},
    }


def test_wp_to_row_extracts_module_from_custom_field():
    row = db._wp_to_row(_wp({
        "customField14": {
            "href": "/api/v3/custom_options/64",
            "title": "Терра - Пользователи",
        }
    }))
    assert row["module_id"] == 64
    assert row["module_name"] == "Терра - Пользователи"


def test_wp_to_row_module_is_none_when_custom_field_absent():
    row = db._wp_to_row(_wp())
    assert row["module_id"] is None
    assert row["module_name"] is None


def test_wp_to_row_module_is_none_when_custom_field_is_empty():
    row = db._wp_to_row(_wp({"customField14": {"href": None, "title": None}}))
    assert row["module_id"] is None
    assert row["module_name"] is None
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_wp_row.py -v`
Expected: 3 FAILED with `KeyError: 'module_id'`

- [ ] **Step 3: Add the module mapping to `src/db.py`**

Add the constant just above `_COLUMNS` (currently `src/db.py:52`):

```python
# OpenProject custom field holding "Модуль". Re-verify after any OpenProject
# form-configuration change with:
#   GET /api/v3/work_packages/schemas/13-7  → customField14.name == "Модуль"
MODULE_CF_KEY = "customField14"
```

In `_COLUMNS`, add the two names to the denormalized-reference block — put them
immediately after the `version_id, version_name` entry so the tuple keeps
mirroring the table's column order:

```python
    "responsible_id", "responsible_name", "version_id", "version_name",
    "module_id", "module_name",
    "raw",
```

In `_wp_to_row`, add the two entries immediately after `version_name`:

```python
        "version_id": _link_id(links.get("version")),
        "version_name": _link_title(links.get("version")),
        "module_id": _link_id(links.get(MODULE_CF_KEY)),
        "module_name": _link_title(links.get(MODULE_CF_KEY)),
        "raw": Jsonb(wp),
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_wp_row.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Add an integration test proving the round-trip through Postgres**

Append to `tests/test_db.py`:

```python
def test_module_columns_are_upserted(db_conn):
    wp = _wp(id=1)
    wp["_links"]["customField14"] = {
        "href": "/api/v3/custom_options/64",
        "title": "Терра - Пользователи",
    }
    db.upsert_bug(db_conn, wp)
    with db_conn.cursor() as cur:
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (64, "Терра - Пользователи")


def test_module_columns_are_null_without_custom_field(db_conn):
    db.upsert_bug(db_conn, _wp(id=1))
    with db_conn.cursor() as cur:
        cur.execute("SELECT module_id, module_name FROM bugs WHERE id = 1")
        assert cur.fetchone() == (None, None)
```

- [ ] **Step 6: Run the full suite**

Run: `make test-integration`
Expected: 42 passed (37 from Task 1 + 3 unit + 2 integration)

- [ ] **Step 7: Commit**

```bash
git add src/db.py tests/test_wp_row.py tests/test_db.py
git commit -m "$(cat <<'EOF'
feat(db): keep module_id/module_name fresh on every upsert

Without this the backfill from migration 004 would be a one-shot snapshot:
bugs created or re-assigned to another module after it ran would keep a stale
value until the next manual UPDATE.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The `v_open_bugs` view

**Files:**
- Modify: `db/migrations/004_module_and_open_bugs.sql` (append)
- Test: `tests/test_open_bugs_view.py` (extend)

**Interfaces:**
- Consumes: `bugs.module_name` (Task 1); `is_status_closed(text)` from `003_history_views.sql:5`.
- Produces: view `v_open_bugs` with columns `id, subject, op_created_at, status_name, priority_name, author_name, module_name, assignee_name, priority_rank, age_days, age_bucket, age_bucket_rank`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_open_bugs_view.py`:

```python
def test_view_excludes_closed_statuses(db_conn):
    for i, status in enumerate(
        ["In progress", "Closed", "No issue found", "Rejected", "Developed"], start=1
    ):
        _insert(db_conn, i, status=status)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_open_bugs ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == [1, 5]


def test_view_excludes_soft_deleted_and_non_bug_types(db_conn):
    _insert(db_conn, 1)
    _insert(db_conn, 2, deleted=True)
    _insert(db_conn, 3, type_name="Task")
    _insert(db_conn, 4, type_name="Question")
    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM v_open_bugs ORDER BY id")
        assert [r[0] for r in cur.fetchall()] == [1]


def test_view_substitutes_placeholders_for_missing_module_and_assignee(db_conn):
    _insert(db_conn, 1, module=None, assignee=None)
    with db_conn.cursor() as cur:
        cur.execute("SELECT backfill_bug_modules()")
        cur.execute("SELECT module_name, assignee_name FROM v_open_bugs WHERE id = 1")
        assert cur.fetchone() == ("— без модуля —", "— не назначен —")


def test_view_ranks_priorities_by_urgency_not_alphabetically(db_conn):
    for i, priority in enumerate(["Low", "Immediate", "Normal", "High"], start=1):
        _insert(db_conn, i, priority=priority)
    with db_conn.cursor() as cur:
        cur.execute("SELECT priority_name FROM v_open_bugs ORDER BY priority_rank")
        assert [r[0] for r in cur.fetchall()] == ["Immediate", "High", "Normal", "Low"]


def test_view_ranks_unknown_priority_last(db_conn):
    _insert(db_conn, 1, priority="Immediate")
    _insert(db_conn, 2, priority="Какой-то новый приоритет")
    with db_conn.cursor() as cur:
        cur.execute("SELECT priority_rank FROM v_open_bugs WHERE id = 2")
        assert cur.fetchone()[0] == 9


def test_view_buckets_age_in_chronological_order(db_conn):
    cases = [
        (1, 0, "0–7 дней", 0),
        (2, 7, "0–7 дней", 0),
        (3, 8, "8–30 дней", 1),
        (4, 30, "8–30 дней", 1),
        (5, 31, "31–90 дней", 2),
        (6, 90, "31–90 дней", 2),
        (7, 91, "91–180 дней", 3),
        (8, 180, "91–180 дней", 3),
        (9, 181, "больше 180 дней", 4),
    ]
    for bug_id, age, _, _ in cases:
        _insert(db_conn, bug_id, age_days=age)
    with db_conn.cursor() as cur:
        cur.execute("SELECT id, age_days, age_bucket, age_bucket_rank"
                    " FROM v_open_bugs ORDER BY id")
        rows = cur.fetchall()
    assert rows == [(bug_id, age, bucket, rank) for bug_id, age, bucket, rank in cases]


def test_view_handles_a_missing_creation_date(db_conn):
    _insert(db_conn, 1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE bugs SET op_created_at = NULL WHERE id = 1")
        cur.execute("SELECT age_days, age_bucket, age_bucket_rank"
                    " FROM v_open_bugs WHERE id = 1")
        assert cur.fetchone() == (None, "неизвестно", 9)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_open_bugs_view.py -v --integration`
Expected: the 4 tests from Task 1 PASS, the 7 new ones FAIL with `psycopg.errors.UndefinedTable: relation "v_open_bugs" does not exist`

- [ ] **Step 3: Append the view to the migration**

Append to `db/migrations/004_module_and_open_bugs.sql`:

```sql
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
  AND NOT is_status_closed(b.status_name);

COMMENT ON VIEW v_open_bugs IS
    'Open bugs for the "Открытые баги" dashboard: type=Bug, not soft-deleted, '
    'not in the closed set (Closed / No issue found / Rejected). Author and '
    'created-date filtering is done by dashboard selectors, not here.';
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_open_bugs_view.py -v --integration`
Expected: 11 PASSED

- [ ] **Step 5: Run the full suite**

Run: `make test-integration`
Expected: 49 passed

- [ ] **Step 6: Commit**

```bash
git add db/migrations/004_module_and_open_bugs.sql tests/test_open_bugs_view.py
git commit -m "$(cat <<'EOF'
feat(db): add v_open_bugs view behind the "Открытые баги" dashboard

Bug-only, not soft-deleted, not in the closed set. Author and created-date
stay unfiltered on purpose — they are dashboard selectors, so the default
43-row cut can be widened to all 82 open bugs without a schema change.

Carries priority_rank and age_bucket_rank because DataLens sorts categories
alphabetically, which would otherwise order priorities High/Low/Normal and
put '8–30 дней' after '31–90 дней'.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Deploy, verify against real data, and document the dashboard

**Files:**
- Modify: `README.md` (new section after "Bug trends dashboard", currently `README.md:105-119`)

**Interfaces:**
- Consumes: `v_open_bugs` from Task 3, live on prod after the image lands.
- Produces: no code surface — a build checklist for the DataLens UI.

- [ ] **Step 1: Push and let CD deliver the image**

```bash
git push origin main
```

GitHub Actions builds and pushes `:latest`; the VM's cron pulls every 5 minutes. Total ≤7 minutes. The ghcr PAT was refreshed on 2026-08-03, so this path works.

- [ ] **Step 2: Verify the migration applied on prod**

```bash
ssh dev-admin@192.168.1.31 \
  "sudo -n -u extractor docker compose -f /srv/extractor/docker-compose.yml \
     -f /srv/extractor/docker-compose.datalens.yml \
     -f /srv/extractor/docker-compose.prod.yml \
     exec -T postgres psql -U extractor -d extractor -c \
     \"SELECT count(*) AS open_bugs, count(*) FILTER (WHERE module_name = '— без модуля —') AS no_module FROM v_open_bugs\""
```

Expected: `open_bugs` ≈ 82, `no_module` ≈ 3. Exact numbers drift with real activity — what matters is that the view exists, is non-empty, and that `no_module` is a small minority rather than everything (the latter would mean the backfill silently matched nothing).

- [ ] **Step 3: Sanity-check the module breakdown**

```bash
ssh dev-admin@192.168.1.31 \
  "sudo -n -u extractor docker compose -f /srv/extractor/docker-compose.yml \
     -f /srv/extractor/docker-compose.datalens.yml \
     -f /srv/extractor/docker-compose.prod.yml \
     exec -T postgres psql -U extractor -d extractor -c \
     'SELECT module_name, count(*) FROM v_open_bugs GROUP BY 1 ORDER BY 2 DESC'"
```

Expected: `Терра - Обращения и заявки` largest, then `Терра - Задачи` / `Терра - Почта` / `Терра - Пользователи` / `Терра - Лифты и Стояки`, plus the two rare ones. If every row says `— без модуля —`, the backfill regex is wrong — check `raw->'_links'->'customField14'` on one row.

- [ ] **Step 4: Write the README section**

Insert after the "Bug trends dashboard" section (before the "Gotcha: DataLens auto-aggregates integers as `sum`" heading):

````markdown
### Open bugs dashboard

A third dashboard reproducing the OpenProject view
[query 245](https://projects-customdev.wone-it.ru/projects/dom-zhkkh/work_packages?query_id=245)
("7 Все открытые баги / задачи - по статусам"), with the analytical cuts a
work-package list can't give.

It is deliberately **not** row-for-row identical to the OpenProject view. Two
differences, both chosen on purpose:

- **Bugs only.** Query 245 also includes `Question` and `Task`; the extractor
  pulls only `Bug`.
- **Our closed-set wins.** Query 245 treats everything but `Closed` as open;
  we treat `Closed`, `No issue found` and `Rejected` as closed (per
  `is_status_closed()`).

Net effect: 43 rows in the default view where OpenProject shows 53.

1. *Datasets* → *+ New* → connection `extractor-bugs` → drag in `v_open_bugs`
   → save as `open_bugs`.
2. In the dataset, add one calculated field — it restores the click-through to
   OpenProject that a BI table otherwise loses:

   ```
   bug_link = URL('https://projects-customdev.wone-it.ru/work_packages/' + STR([id]), STR([id]))
   ```

3. Build nine charts from the `open_bugs` dataset:

   | # | Chart | Type | Configuration |
   |---|---|---|---|
   | 1 | Всего открытых | Индикатор | `COUNT([id])` |
   | 2–4 | High / Normal / Low | Индикатор ×3 | `COUNT([id])` + chart filter on `priority_name` |
   | 5 | По статусам | Линейчатая | Y `status_name`, X `COUNT([id])`, sort by measure ↓ |
   | 6 | По модулям | Линейчатая | Y `module_name`, X `COUNT([id])`, sort by measure ↓ |
   | 7 | По исполнителям | Линейчатая | Y `assignee_name`, X `COUNT([id])`, sort by measure ↓ |
   | 8 | Возраст открытых багов | Столбчатая | X `age_bucket` ordered by `age_bucket_rank`, Y `COUNT([id])`, color `priority_name` |
   | 9 | Открытые баги | Таблица | `op_created_at`, `bug_link`, `priority_name`, `subject`, `module_name`, `status_name`, `assignee_name`, `author_name`, `age_days`; sort `priority_rank` ↑ then `id` ↑ |

4. *Dashboards* → *+ New* → drop the nine charts on the grid. Suggested layout:
   indicator row (3 grid columns each), then 5+6, then 7+8, then chart 9 full
   width. Save as `Открытые баги`.
5. Add four selectors, all bound to the `open_bugs` dataset:

   | Selector | Field | Default |
   |---|---|---|
   | Автор | `author_name` | Ольга Черняева, Nikita Avdonin, Полина Маренко, Ольга Коцур, Кирилл Занин, Ольга Сергеевна Бровина, Владимир Бабушкин, Аркадий Лоскутов, Екатерина Губова |
   | Создано | `op_created_at` | from 2026-03-01, no upper bound |
   | Приоритет | `priority_name` | all |
   | Модуль | `module_name` | all |

   These defaults reproduce the 43-row cut. Clearing "Автор" widens it to 60;
   clearing the date filter too, to 82.

Two limitations worth knowing before you start:

- DataLens OSS tables have no collapsible groups, so the "High (10) / Normal
  (11) / Low (33)" accordion from OpenProject can't be reproduced. Charts 2–4
  carry those counts instead and the table is flat, sorted by priority.
- `age_days` counts from bug creation, not from entry into the current status.
  For time-in-status see `v_bug_time_in_status` and the `Bug trends` dashboard.
````

- [ ] **Step 5: Commit and push**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: build checklist for the "Открытые баги" dashboard

Records why the dashboard shows 43 rows where OpenProject shows 53 — Bug-only
plus our closed-set — so the difference doesn't get reported as a bug later.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

- [ ] **Step 6: Hand off the DataLens assembly**

The dashboard itself is built by hand in the DataLens UI at
<http://192.168.1.31/> following the README section just written. There is no
config-as-code path in this deployment: DataLens stores dashboards in its own
`datalens-postgres` (`pg-us-db` volume), which is why the README checklist *is*
the reproducible artifact — same arrangement as `Bugs overview` and
`Bug trends`.

---

## Self-review notes

**Spec coverage.** Every locked decision maps to a task: decisions 1–4 (scope
cuts) → the `type_name = 'Bug'` filter and column list in Task 3; 5 + 9 (module
as a real column) → Tasks 1–2; 6 (closed-set) → `is_status_closed()` in Task 3;
7 (selectors) → README step 5 in Task 4, with the deliberate absence of
author/date filters documented in the view's own comment; 8 (dashboard shape) →
README step 3; 10 (image delivery) → Task 4 steps 1–3.

**Deviation from the spec, deliberate.** The spec put the `_wp_to_row` unit
tests in `tests/test_db.py`. That file has a module-level
`pytestmark = pytest.mark.integration`, so they would be skipped in `make test`.
They live in a new `tests/test_wp_row.py` instead, and `test_db.py` gets the
Postgres round-trip tests that genuinely need a DB.

**Addition beyond the spec.** `age_bucket` handles `op_created_at IS NULL`
(→ `'неизвестно'`, rank 9). The spec's `CASE` chain would have silently
labelled such a row `'больше 180 дней'`. No production row has a NULL creation
date today, so this is cheap insurance rather than a fix.
