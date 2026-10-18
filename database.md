# Database

**Engine:** PostgreSQL 15 in Docker Compose; SQLite for local development
**ORM:** SQLAlchemy (`declarative_base` + `Column`)
**Migrations:** Alembic, `backend/migrations/versions/`
**Models:** `backend/app/models/`

Related: [architecture.md](architecture.md) · [api.md](api.md)

---

## 1. Connection

`backend/app/core/database.py` builds one engine at import time:

```
DATABASE_URL set?          → use it verbatim
otherwise                  → postgresql://USER:PASSWORD@SERVER:PORT/DB
```

| Setting | Default | Notes |
|---------|---------|-------|
| `DATABASE_URL` | *unset* | Set to `sqlite:///./shelf_monitoring.db` for file-backed dev |
| `POSTGRES_SERVER` | `localhost` | `db` inside Compose |
| `POSTGRES_USER` | `postgres` | |
| `POSTGRES_PASSWORD` | `postgres` | |
| `POSTGRES_DB` | `shelf_monitoring` | |
| `POSTGRES_PORT` | `5432` | |

- SQLite connections use `check_same_thread=False` (background tasks and the request
  thread share the engine).
- PostgreSQL connections use `pool_pre_ping=True`.
- `SessionLocal` = `sessionmaker(autocommit=False, autoflush=False)`; FastAPI's `get_db`
  yields a session and always closes it.
- Background jobs open their **own** session because the request session is closed by the
  time `BackgroundTasks` runs.

---

## 2. Entity-relationship diagram

```
┌─────────┐
│ stores  │
│ id (PK) │
└────┬────┘
     │ 1
     ├──────────────< cameras >──────────────┐
     │ 1                 │ 1                 │
     │                   │                    │
     │              ┌────▼─────┐              │
     │              │ captures │              │
     │              │ id (PK)  │              │
     │              └─┬──────┬─┘              │
     │           1..N │      │ 1..N           │
     │        ┌───────▼──┐  ┌▼──────────┐     │
     │        │detections│  │shelf_     │     │
     │        └──────────┘  │reports    │     │
     │                      └─┬─────────┘     │
     │                    1..N│               │
     │                    ┌───▼────┐          │
     │                    │ alerts │          │
     │                    └────────┘          │
     └──────────────< users >─────────────────┘
                   1..N per store
```

Cardinality summary:

| Relationship | Type | Cascade |
|--------------|------|---------|
| `stores` → `cameras` | 1 : N | delete-orphan |
| `stores` → `users` | 1 : N | delete-orphan |
| `cameras` → `captures` | 1 : N | delete-orphan |
| `captures` → `detections` | 1 : N | delete-orphan |
| `captures` → `shelf_reports` | 1 : N | delete-orphan |
| `shelf_reports` → `alerts` | 1 : N | delete-orphan |

Database-level FKs use `ON DELETE CASCADE`, so integrity holds even for rows deleted
outside the ORM.

---

## 3. Tables

### 3.1 `stores`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `name` | String | no | — | Display name |
| `location` | String | yes | `NULL` | Free-text location |

- No uniqueness constraint on `(name, location)`. The seed script keeps re-runs
  idempotent by looking up an existing row before inserting.
- Owns cameras and users.

### 3.2 `users`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `email` | String | no | — | **Unique index**, stored lower-cased |
| `hashed_password` | String | no | — | bcrypt hash; never returned |
| `store_id` | Integer | no | — | FK → `stores.id`, `ON DELETE CASCADE`, indexed |
| `created_at` | DateTime(tz) | no | `now()` | Server default |

- One row per dashboard login. Signup creates a new store per account, so the normal
  flow is one account per store; the schema does not forbid several, and alert delivery
  emails every account in the store.
- Email normalisation happens in the Pydantic schema before validation, so
  `Owner@Shop.com` and `owner@shop.com` are the same account.

### 3.3 `cameras`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `store_id` | Integer | no | — | FK → `stores.id`, `ON DELETE CASCADE` |
| `aisle` | String | no | — | Label used in alerts and reports |
| `name` | String | no | — | Human name, e.g. "Aisle 4 East" |

- `store_id` is **always** assigned from the authenticated user on create; the API does
  not accept a client-supplied store.

### 3.4 `captures`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `camera_id` | Integer | no | — | FK → `cameras.id`, `ON DELETE CASCADE` |
| `image_url` | String | no | — | `/uploads/<uuid>.<ext>` |
| `captured_at` | DateTime(tz) | no | Python UTC now + `now()` | Explicit UTC |
| `status` | String | no | `'processing'` | `processing` \| `ready` \| `failed` |
| `error_message` | String | yes | `NULL` | Set only when `status = 'failed'` |

- The file itself lives in `UPLOAD_DIR`, not in the database.
- `ready` is the DB success state; the API maps it to `completed`.
- `error_message` is truncated to 1000 characters when persisted.

### 3.5 `detections`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `capture_id` | Integer | no | — | FK → `captures.id`, `ON DELETE CASCADE` |
| `class` | String | no | — | Mapped from ORM field `class_` (reserved keyword) |
| `bbox` | JSON | no | — | `[xmin, ymin, xmax, ymax]` in image pixels |
| `confidence` | Float | no | — | Detector score `0..1` |

- One row per reported empty-space region. `class` is `empty` for the shipped model.
- `bbox` is dialect-portable JSON (PostgreSQL `JSON`, SQLite text).

### 3.6 `shelf_reports`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `capture_id` | Integer | no | — | FK → `captures.id`, `ON DELETE CASCADE` |
| `fill_percentage` | Float | no | — | **Fraction `0..1`**, not `0..100` |
| `low_confidence` | Boolean | no | `false` | See semantics below |
| `empty_regions_json` | JSON | yes | `NULL` | List of bboxes as returned by the analyzer |
| `urgency` | String | no | — | `low` \| `medium` \| `high` |
| `llm_summary` | String | yes | `NULL` | Human-readable assessment |
| `recommended_action` | String | yes | `NULL` | Concrete next action |
| `created_at` | DateTime(tz) | no | Python UTC now + `now()` | Explicit UTC |

**`low_confidence` semantics.** When the analyzer produces zero detections *and* does not
mark the result as assessed, `fill_percentage` is the vacuous `1.0` ("nothing empty
found"), which is not evidence the shelf is full. Such rows:

- are rendered as **"Unable to assess"** rather than 100 % full,
- are excluded from alerting,
- are excluded as a `previous_fill` / trend baseline.

A zero-detection result that *was* assessed (quality gate passed, heuristic measured no
gaps) is a legitimate full shelf and keeps `low_confidence = false`.

### 3.7 `alerts`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `shelf_report_id` | Integer | no | — | FK → `shelf_reports.id`, `ON DELETE CASCADE` |
| `sent_to_slack` | Boolean | no | `false` | Slack delivery succeeded |
| `sent_to_email` | Boolean | no | `false` | At least one email delivered |
| `email_recipients` | String(1000) | yes | `NULL` | Comma-joined addresses actually reached |
| `sent_at` | DateTime(tz) | yes | `NULL` | Set only when Slack succeeded |

- One alert row per **high-urgency, confident** report, created whether or not a channel
  is configured: the row records the attempt (`sent_to_* = false` when nothing was
  configured or delivery failed), so the dashboard can show what happened.
- `sent_at` historically means "Slack accepted the message"; email timestamps are not
  stored separately.

### 3.8 `products` (reserved)

| Column | Type | Null | Default |
|--------|------|------|---------|
| `id` | Integer | no | autoincrement |
| `name` | String | no | — |
| `upc` | String | no | unique index |
| `price` | Float | no | — |
| `expected_quantity` | Integer | no | 0 |
| `current_quantity` | Integer | no | 0 |
| `created_at` | DateTime(tz) | yes | `now()` |
| `updated_at` | DateTime(tz) | yes | `NULL` |

No API endpoint or pipeline reads or writes this table today; it is a placeholder for
SKU-level inventory.

---

## 4. Migration chain

Alembic config: `backend/alembic.ini` (`script_location = migrations`). Run from
`backend/` so the relative path resolves.

| Order | Revision | Down-revision | Change |
|-------|----------|---------------|--------|
| 1 | `e71b2d56a38f` | — | Initial schema: stores, cameras, captures, detections, shelf_reports, alerts, products |
| 2 | `f2a9c4b7d1e3` | `e71b2d56a38f` | Add `shelf_reports.recommended_action` |
| 3 | `c3d5e7f9a1b2` | `f2a9c4b7d1e3` | Add `captures.status`, `captures.error_message` |
| 4 | `b4d8f1a6c2e9` | `c3d5e7f9a1b2` | Add `users` (+ unique email index, store index) |
| 5 | `d5e8b3f1c7a4` | `b4d8f1a6c2e9` | Add `shelf_reports.low_confidence`; backfill `urgency='unknown'` rows |
| 6 | `b8f1c4d7e2a6` | `d5e8b3f1c7a4` | Add `alerts.sent_to_email`, `alerts.email_recipients` |

Notes:

- Batch mode is used for `ALTER TABLE` operations so SQLite works too.
- `captures.status` and `low_confidence` are added with `server_default` so existing rows
  stay valid.
- The `low_confidence` migration also recomputes `urgency` for legacy `unknown` rows from
  the fill thresholds (`≥0.6` low, `≥0.3` medium, else high).

Commands (from `backend/`):

```bash
alembic upgrade head
alembic current
alembic revision --autogenerate -m "describe the change"
alembic downgrade -1
```

The Compose entrypoint runs `alembic upgrade head` on every container start, after
confirming PostgreSQL accepts a real connection.

---

## 5. Indexes

| Table | Index | Unique |
|-------|-------|--------|
| all tables | `ix_<table>_id` on `id` | no |
| `users` | `ix_users_email` | **yes** |
| `users` | `ix_users_store_id` | no |
| `products` | `ix_products_upc` | **yes** |

Foreign keys other than `users.store_id` are not explicitly indexed. Read paths join
`captures → cameras` and `reports → captures`, so on a large dataset those joins would
benefit from composite indexes (`captures(camera_id)`, `shelf_reports(capture_id)`,
`alerts(shelf_report_id)`). This is a known scalability gap, not a correctness issue.

---

## 6. Invariants and rules

1. **Tenant scope is derived, never supplied.** Every query is anchored on
   `Camera.store_id == current_user.store_id` (or a join that reaches it).
2. **`fill_percentage` is a fraction.** Multiply by 100 in the UI; do not change the unit
   without updating `Dashboard`, `Trends`, `ShelfDetail`, exports, and alert text.
3. **`urgency` only holds `low`/`medium`/`high`.** No `unknown` value is written today.
4. **A confident high urgency creates at most one alert per report.**
5. **Low-confidence reports never alert and never seed a trend.**
6. **Timestamps are UTC.** Writes use Python `datetime.now(timezone.utc)`; the DB default
   is a fallback. SQLite returns naive values, so serialisation attaches UTC explicitly.
7. **`captures.status` is terminal after the pipeline.** `processing → ready` or
   `processing → failed`; a failed capture keeps its `error_message`.
8. **Deleting a store deletes everything it owns** via cascades.
9. **Email addresses in `users` are unique and lower-cased.**

---

## 7. Representative queries

Reports for a store with context (what the API does):

```sql
SELECT shelf_reports.*, cameras.store_id, cameras.aisle, cameras.name, stores.name
FROM shelf_reports
JOIN captures ON captures.id = shelf_reports.capture_id
JOIN cameras  ON cameras.id = captures.camera_id
JOIN stores   ON stores.id = cameras.store_id
WHERE cameras.store_id = :store_id
ORDER BY shelf_reports.created_at DESC, shelf_reports.id DESC
LIMIT :limit OFFSET :offset;
```

Previous confident fill for a camera:

```sql
SELECT shelf_reports.fill_percentage
FROM shelf_reports
JOIN captures ON captures.id = shelf_reports.capture_id
WHERE captures.camera_id = :camera_id
  AND captures.id < :current_capture_id
  AND shelf_reports.low_confidence = false
ORDER BY captures.id DESC
LIMIT 1;
```

Alert recipients for the store that owns a camera:

```sql
SELECT email FROM users WHERE store_id = :store_id;
```

---

## 8. Local data and seeding

```bash
cd backend
alembic upgrade head
python scripts/seed.py
```

`scripts/seed.py` is idempotent:

- stores looked up by `name`,
- cameras by `(store_id, name)`,
- sample captures by their deterministic image URL,
- demo account created only when `users` is empty.

It creates 2 stores, 5 cameras, 5 captures (with generated JPEGs in `backend/uploads/`),
their reports and detections, 2 high-urgency alerts, and the demo login documented in
[README.md](README.md#demo-login).

Resetting local SQLite:

```bash
rm backend/shelf_monitoring.db
cd backend && alembic upgrade head && python scripts/seed.py
```

Resetting Compose PostgreSQL:

```bash
docker compose down -v      # removes the pgdata volume
docker compose up --build
```

---

## 9. Operational notes

- **Backups:** the SQLite file is the whole database; for PostgreSQL use `pg_dump`.
- **Uploads are separate:** `backend/uploads/` (or `UPLOAD_DIR`) is not covered by a
  database dump. Back it up alongside the DB or report images will 404.
- **Timezone caveat:** SQLite stores naive datetimes; all API responses add `Z`, so
  consumers see correct instants. Direct SQL inspection will show naive values.
- **Engine creation is import-time:** changing `DATABASE_URL` requires a process restart.
- **Migrations are not run automatically outside Compose;** run `alembic upgrade head`
  before starting a manually managed deployment.
