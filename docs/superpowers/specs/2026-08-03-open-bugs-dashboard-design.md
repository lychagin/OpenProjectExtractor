# Open bugs dashboard (design)

Status: design approved 2026-08-03. Ready for `/writing-plans`.

## Goal

Reproduce, as a third DataLens dashboard, the OpenProject view
[query 245](https://projects-customdev.wone-it.ru/projects/dom-zhkkh/work_packages?query_id=245)
— "7 Все открытые баги / задачи - по статусам" — adding the analytical cuts that a
work-package list cannot give: counts by status, module, assignee and age, with the
original's filters exposed as dashboard selectors.

## Source view, as it actually is

Read from `/api/v3/queries/245` on 2026-08-03, not from the screenshot:

| Aspect | Value |
|---|---|
| Filters | Type ∈ {Bug, Question, Task}; Author ∈ 9 named users; CreatedAt between 2026-03-01 and 2027-06-30; Project = ДОМ ЖКХ; Status ≠ `Closed` |
| Group by | Priority |
| Sort | Status ↓, then ID ↑ |
| Columns | Создано, ID, Приоритет, Тема, Тип, Модуль, Категория, Статус, Назначенный, Автор, Предполагаемое время |
| Size | 53 rows (Bug 47, Question 4, Task 2) |

`Модуль` is OpenProject custom field `customField14` (schema `13-7`, type `CustomOption`).
It arrives as `_links.customField14 = {href: /api/v3/custom_options/<id>, title: "Терра - …"}`.
`Категория` is the standard `_links.category`.

## Decisions (locked)

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| 1 | Work-package types | `Bug` only | User scoped it down; extractor already pulls only `Bug`, so no extractor change |
| 2 | `Категория` column | Dropped | Not wanted |
| 3 | `Тип` column | Dropped | Constant `BUG` after decision 1 |
| 4 | `Предполагаемое время` column | Dropped | Populated on 0 of 43 rows |
| 5 | `Модуль` | Kept, as a first-class column | Main analytical axis of the dashboard |
| 6 | Closed-set definition | Ours wins: `Closed`, `No issue found`, `Rejected` | Diverges from query 245, which treats everything but `Closed` as open; user chose ours |
| 7 | Author + CreatedAt filters | Dashboard selectors, not baked into SQL | Lets the team widen the view in one click; keeps 9 surnames out of a migration |
| 8 | Dashboard shape | Indicators + 4 charts + detail table | User picked over table-only |
| 9 | `Модуль` storage | Real column, backfilled from `raw` | User chose it over reading `raw` in the view |
| 10 | Image delivery to prod | Fix the ghcr PAT | Done 2026-08-03; CD verified working again |

### Consequence of decisions 1 + 6 — the row count changes

The dashboard is deliberately *not* row-for-row identical to query 245:

| Cut | Open bugs |
|---|---|
| All open bugs in the project (our closed-set) | 82 |
| + author whitelist | 60 |
| + created ≥ 2026-03-01 | 59 |
| + both — the selectors' default state | **43** |

Query 245 shows 53 because it also counts `Question`/`Task` (+6) and treats
`Rejected`/`No issue found` as open (+4). 43 is the intended number.

## Architecture

```
bugs.raw (jsonb, refreshed every sync cycle)
   │  _links.customField14.{href,title}
   ▼
004_module_and_open_bugs.sql
   ALTER TABLE bugs ADD module_id / module_name   ← backfill from raw (idempotent)
   CREATE OR REPLACE VIEW v_open_bugs             ← type=Bug, NOT is_status_closed()
   │
   ├── src/db.py keeps module_id/module_name fresh on every upsert
   ▼
DataLens dataset `open_bugs` (1) → 9 widgets → dashboard `Открытые баги`
   selectors: author_name, op_created_at, priority_name, module_name
```

No new services, no extractor logic change beyond two more denormalized columns.

## SQL surface (`db/migrations/004_module_and_open_bugs.sql`)

### Columns

```sql
ALTER TABLE bugs ADD COLUMN IF NOT EXISTS module_id   integer;
ALTER TABLE bugs ADD COLUMN IF NOT EXISTS module_name text;
CREATE INDEX IF NOT EXISTS bugs_module_name_idx ON bugs (module_name);
```

### Backfill

`raw` already holds `customField14` for all 1220 bugs, so no re-fetch from
OpenProject is needed. The `IS DISTINCT FROM` guard makes this a no-op on every
run after the first — migrations execute on every container start.

```sql
UPDATE bugs SET
    module_id   = NULLIF(regexp_replace(raw->'_links'->'customField14'->>'href', '^.*/', ''), '')::integer,
    module_name = raw->'_links'->'customField14'->>'title'
WHERE module_name IS DISTINCT FROM raw->'_links'->'customField14'->>'title';
```

### View

```sql
CREATE OR REPLACE VIEW v_open_bugs AS
SELECT
    id,
    subject,
    op_created_at,
    status_name,
    priority_name,
    author_name,
    COALESCE(module_name,   '— без модуля —')  AS module_name,
    COALESCE(assignee_name, '— не назначен —') AS assignee_name,
    CASE priority_name
        WHEN 'Immediate' THEN 0 WHEN 'High' THEN 1
        WHEN 'Normal'    THEN 2 WHEN 'Low'  THEN 3 ELSE 9
    END AS priority_rank,
    (current_date - op_created_at::date) AS age_days,
    CASE
        WHEN (current_date - op_created_at::date) <=   7 THEN '0–7 дней'
        WHEN (current_date - op_created_at::date) <=  30 THEN '8–30 дней'
        WHEN (current_date - op_created_at::date) <=  90 THEN '31–90 дней'
        WHEN (current_date - op_created_at::date) <= 180 THEN '91–180 дней'
        ELSE 'больше 180 дней'
    END AS age_bucket,
    CASE
        WHEN (current_date - op_created_at::date) <=   7 THEN 0
        WHEN (current_date - op_created_at::date) <=  30 THEN 1
        WHEN (current_date - op_created_at::date) <=  90 THEN 2
        WHEN (current_date - op_created_at::date) <= 180 THEN 3
        ELSE 4
    END AS age_bucket_rank
FROM bugs
WHERE deleted_at IS NULL
  AND type_name = 'Bug'
  AND NOT is_status_closed(status_name);
```

Three points worth stating explicitly:

- **`COALESCE` on module and assignee** — 3 open bugs have no module and 6 no
  assignee. Without this DataLens renders an unlabeled `null` category in bars
  and selectors.
- **Rank columns exist because DataLens sorts categories alphabetically.**
  Without `priority_rank` the order is High/Low/Normal; without
  `age_bucket_rank`, `'8–30 дней'` sorts after `'31–90 дней'`.
- **`age_days` counts from creation, not from entry into the current status.**
  Time-in-status needs `bug_history` and is out of scope here — `v_bug_time_in_status`
  already covers it in aggregate.

### Priority ordering

OpenProject priorities are `Low(7) < Normal(8) < High(9) < Immediate(10)`.
Ordering is by name rather than by `priority_id` because the id ordering is an
accident of insertion; names are stored in English regardless of UI locale
(the client sends no `Accept-Language`).

## Python surface (`src/db.py`)

```python
# OpenProject custom field holding "Модуль". Verify with:
#   GET /api/v3/work_packages/schemas/13-7  → customField14.name == "Модуль"
MODULE_CF_KEY = "customField14"
```

`module_id` / `module_name` join `_COLUMNS` and `_wp_to_row`, read via the
existing `_link_id` / `_link_title` helpers. After deploy the column refreshes on
every sync cycle alongside status and assignee.

## DataLens surface

One dataset, `open_bugs`, over `v_open_bugs`, on the existing `extractor-bugs`
connection.

### Calculated field (dataset)

```
bug_link = URL('https://projects-customdev.wone-it.ru/work_packages/' + STR([id]), STR([id]))
```

This is the one deliberate improvement over the original: it restores the
click-through to the work package that a BI table otherwise loses.

### Selectors

| Selector | Field | Default |
|---|---|---|
| Автор | `author_name` | the 9 users from query 245 |
| Создано | `op_created_at` | from 2026-03-01, no upper bound |
| Приоритет | `priority_name` | all |
| Модуль | `module_name` | all |

Defaults reproduce the 43-row cut. Clearing "Автор" widens to 60; clearing the
date too, to 82.

### Widgets

| # | Widget | Type | Configuration |
|---|---|---|---|
| 1 | Всего открытых | Индикатор | `COUNT([id])` |
| 2–4 | High / Normal / Low | Индикатор ×3 | `COUNT([id])` + chart filter on `priority_name` |
| 5 | По статусам | Линейчатая | Y `status_name`, X `COUNT([id])`, sort by measure ↓ |
| 6 | По модулям | Линейчатая | Y `module_name`, X `COUNT([id])`, sort by measure ↓ |
| 7 | По исполнителям | Линейчатая | Y `assignee_name`, X `COUNT([id])`, sort by measure ↓ |
| 8 | Возраст открытых багов | Столбчатая | X `age_bucket` (ordered by `age_bucket_rank`), Y `COUNT([id])`, color `priority_name` |
| 9 | Открытые баги | Таблица | Создано, `bug_link`, Приоритет, Тема, Модуль, Статус, Назначенный, Автор, Дней; sort `priority_rank` ↑ then `id` ↑ |

Layout: indicator row (3 grid columns each), then 5+6, then 7+8, then the table
full width.

**Use `COUNT([id])`, never the default aggregation.** DataLens auto-aggregates
integer columns as `sum`, and `sum([id])` is a sum of primary keys. This is the
same trap documented for the first two dashboards.

### Known limitation

DataLens OSS tables have no collapsible groups, so the "High (7) / Normal (4) /
Low (32)" accordion from OpenProject cannot be reproduced. Widgets 2–4 carry
those counts instead, and the table is flat, sorted by priority.

## Deployment

CD was broken when this was designed — the ghcr PAT on the VM had expired, so
`docker compose pull` failed and new images could not reach prod. It was
refreshed on 2026-08-03 and verified (`docker pull` succeeds; cron recreated the
container). Because `Dockerfile` does `COPY db/ ./db/`, the migration ships
inside the image and applies via `bootstrap_schema()` at extractor startup — so
the normal push-to-main path now works end to end (≤7 min).

No manual `psql` step is required. Should CD break again, the fallback is to run
`004_module_and_open_bugs.sql` by hand against the prod DB; it is idempotent, so
the later image arrival is harmless.

## Testing

TDD, matching the repo's existing split (33 tests green today).

**Unit** (`tests/test_db.py`, no DB):

- `_wp_to_row` extracts `module_id` / `module_name` from `_links.customField14`.
- Absent `customField14` yields `None` for both, not `KeyError`.

**Integration** (`tests/test_open_bugs_view.py`, opt-in `--integration`):

- Backfill populates `module_name` from `raw`, and a second run changes nothing
  (idempotence).
- `v_open_bugs` excludes soft-deleted rows, closed statuses, and non-`Bug` types.
- `COALESCE` placeholders appear for NULL module / assignee.
- `priority_rank` orders Immediate → Low; `age_bucket_rank` orders the buckets.

Integration tests run inside a transaction rolled back at teardown, per
`tests/conftest.py`, so they are safe against the populated DB.

## Out of scope

- Question / Task work packages (extractor stays `Bug`-only).
- `Категория` column.
- Time-in-current-status (needs `bug_history`).
- Reconciling our closed-set with query 245's.
- DataLens dashboard config as code — it lives in `datalens-postgres` and is
  rebuilt from the README section, as with the two existing dashboards.
