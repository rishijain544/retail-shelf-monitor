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
| `users` → `email_verification_codes` | 1 : N | delete-orphan |
| `users` → `mfa_recovery_codes` | 1 : N | delete-orphan |

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
| `email_verified` | Boolean | no | `false` | Address proven by an emailed code |
| `mfa_enabled` | Boolean | no | `false` | Authenticator enrolled and confirmed |
| `mfa_secret_encrypted` | String | yes | `NULL` | Fernet ciphertext of the base32 TOTP secret |
| `mfa_last_used_step` | Integer | yes | `NULL` | Last accepted TOTP step (replay protection) |
| `mfa_exempt` | Boolean | no | `false` | Password-only account (demo); never settable via the API |
| `failed_login_attempts` | Integer | no | `0` | Consecutive wrong passwords |
| `login_locked_until` | DateTime(tz) | yes | `NULL` | Password lockout expiry |
| `failed_mfa_attempts` | Integer | no | `0` | Consecutive wrong second factors |
| `mfa_locked_until` | DateTime(tz) | yes | `NULL` | MFA lockout expiry |
| `password_changed_at` | DateTime(tz) | yes | `NULL` | Last password reset; tokens with an older `iat` are refused |

- One row per dashboard login. Signup creates a new store per account, so the normal
  flow is one account per store; the schema does not forbid several, and alert delivery
  emails every account in the store.
- Email normalisation happens in the Pydantic schema before validation, so
  `Owner@Shop.com` and `owner@shop.com` are the same account.
- **`mfa_secret_encrypted` is ciphertext, not a secret.** It is encrypted with
  `MFA_ENCRYPTION_KEY` (Fernet), so a database dump alone does not yield working
  authenticator seeds. Losing the key makes every enrolled account unreadable; the only
  fix is `backend/scripts/reset_mfa.py <email>`. The plaintext base32 secret is returned
  exactly once, by `POST /auth/mfa/setup/start`.
- **`mfa_exempt` is not writable through any API.** Only `scripts/seed.py`,
  `app/services/demo_account.py` and `scripts/reset_mfa.py` set it. The shared demo account
  is exempt because its `.test` address cannot receive mail and the public cannot hold its
  authenticator.
- **Lockout counters reset on success**, and the counter is zeroed when a lockout is set,
  so the account gets a full set of attempts once the lockout expires.
- Timestamps are compared through `app/core/clock.as_utc()`: SQLite returns naive datetimes
  for `DateTime(timezone=True)`, and comparing one to an aware `now` raises `TypeError`.
- The migration grandfathered existing rows with `email_verified = true` and left
  `mfa_enabled = false`, so an upgrade never locks out a live deployment.
- **`password_changed_at` is truncated to whole seconds when written**, because JWT `iat`
  claims are second-granular: a finer value would reject the first session created right
  after a successful reset. It is `NULL` for accounts that never reset, and `NULL` means
  "no token is stale by this rule". A reset does **not** touch MFA state,
  `mfa_locked_until` or `mfa_recovery_codes`.

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

### 3.8 `email_settings`

Singleton row (`id = 1`) holding the alert-email transport verified through
**Alerts → Email setup**. It is not tenant-scoped: one sending identity serves the whole
installation, and recipients are still resolved per store from `users` at send time.

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | — | PK; always `1` |
| `transport` | String(10) | yes | `NULL` | `api` \| `smtp`; the active channel |
| `resend_api_key` | String(255) | yes | `NULL` | API transport secret (never returned by the API) |
| `email_from` | String(320) | yes | `NULL` | Sender for the API transport |
| `alert_email_to` | String(1000) | yes | `NULL` | Global recipient override |
| `smtp_host` | String(255) | yes | `NULL` | Relay host |
| `smtp_port` | Integer | yes | `NULL` | 587 (STARTTLS) or 465 (SSL) |
| `smtp_user` | String(320) | yes | `NULL` | Relay login |
| `smtp_password` | String(512) | yes | `NULL` | App password / relay secret |
| `smtp_from` | String(320) | yes | `NULL` | From: address; falls back to `smtp_user` |
| `smtp_use_tls` | Boolean | no | `true` | STARTTLS |
| `smtp_use_ssl` | Boolean | no | `false` | Implicit TLS |
| `updated_at` | DateTime(tz) | no | `now()` | Last install |

Rules:

- **One active transport.** Saving an API key nulls the SMTP columns; saving SMTP nulls the
  API columns. Otherwise `email_service` could route through a stale relay.
- **Precedence at startup.** `email_settings.load_into_settings()` applies the row over
  `RESEND_API_KEY` / `SMTP_*` environment variables. No row means env vars stay in charge.
- **Why a table and not `.env`:** a deployed container is read-only or ephemeral, so the old
  env-file write was lost on every redeploy. This row is what keeps email alerting working
  after deployment.
- Deleting the row (or downgrading the migration) returns the installation to env-var
  configuration; it does not send anything.

### 3.9 `products` (reserved)

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

### 3.10 `email_verification_codes`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `user_id` | Integer | no | — | FK → `users.id`, `ON DELETE CASCADE`, indexed |
| `code_hash` | String(64) | no | — | `HMAC-SHA256(pepper, "<user_id>:<code>")` hex |
| `expires_at` | DateTime(tz) | no | — | `created_at + VERIFICATION_CODE_TTL_MINUTES` |
| `attempts` | Integer | no | `0` | Wrong guesses; at `MAX_CODE_ATTEMPTS` the row is burned |
| `created_at` | DateTime(tz) | no | Python UTC now + `now()` | Also drives the 60 s resend cooldown |
| `consumed_at` | DateTime(tz) | yes | `NULL` | Set on success, expiry, or burn |

- Only rows with `consumed_at IS NULL` can verify. Issuing a new code consumes every
  earlier unconsumed row for that account, so at most one code is ever live.
- The digest is keyed with `SECRET_KEY`-derived material, not a bare SHA-256: six digits is
  a million candidates, which an unkeyed hash would not protect.
- Rows are kept after use rather than deleted — the cooldown and attempt limit need the
  history.

### 3.11 `mfa_recovery_codes`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `user_id` | Integer | no | — | FK → `users.id`, `ON DELETE CASCADE`, indexed |
| `code_hash` | String(255) | no | — | bcrypt digest; plaintext never stored |
| `used_at` | DateTime(tz) | yes | `NULL` | Single use |

- Ten rows per enrolment, replaced wholesale by `/auth/mfa/recovery-codes/regenerate` and
  deleted by `scripts/reset_mfa.py`. The plaintext codes are returned once, in the response
  that creates them.
- bcrypt runs at a lower cost factor here than for passwords (10 vs the context default):
  a login checks all unused rows, so the default cost would add seconds to every attempt
  without adding real protection. What protects these codes is 32 bits of entropy plus the
  5-attempt lockout.

### 3.12 `password_reset_tokens`

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `id` | Integer | no | autoincrement | PK, indexed |
| `user_id` | Integer | no | — | FK → `users.id`, `ON DELETE CASCADE`, indexed |
| `token_hash` | String(64) | no | — | **Unique index**; SHA-256 hex of the emailed token |
| `expires_at` | DateTime(tz) | no | — | `PASSWORD_RESET_TTL_MINUTES` after creation |
| `used_at` | DateTime(tz) | yes | `NULL` | Set on use *and* when a newer request supersedes it |
| `created_at` | DateTime(tz) | no | `now()` | Server default; drives the per-account cooldown |
| `requested_ip` | String(64) | yes | `NULL` | Requester address for support/abuse triage |

- **The token itself is never stored.** What is stored is `sha256(token)`, where the token
  is 256 bits of `secrets.token_urlsafe(32)` — a dump yields no working link. Lookups
  compare with `hmac.compare_digest`.
- One valid row per account: creating a token marks every earlier unused row `used_at`, and
  a reset marks all remaining rows used. Rows are kept rather than deleted so the cooldown
  and the audit trail survive.
- The shared demo account (`mfa_exempt`) and every `.test` address never get a row, so
  neither can be reset.

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
| 7 | `a7c1e9f4d2b8` | `b8f1c4d7e2a6` | Add `email_settings` (verified transport survives redeploys) |
| 8 | `c9f4a2d6e8b1` | `a7c1e9f4d2b8` | Add MFA + verification columns to `users`; add `email_verification_codes`, `mfa_recovery_codes`; grandfather existing users as verified |
| 9 | `f1a4c7d9b2e6` | `c9f4a2d6e8b1` | Add `users.password_changed_at`; add `password_reset_tokens` |

Notes:

- Batch mode is used for `ALTER TABLE` operations so SQLite works too.
- `captures.status` and `low_confidence` are added with `server_default` so existing rows
  stay valid.
- The `low_confidence` migration also recomputes `urgency` for legacy `unknown` rows from
  the fill thresholds (`≥0.6` low, `≥0.3` medium, else high).
- Revision 9 uses `op.batch_alter_table`, `sa.func.now()` and no raw PostgreSQL-only SQL,
  so it applies unchanged to SQLite and PostgreSQL. Check both: the SQLite chain in the
  suite, and `python scripts/check_migrations_postgres.py` against a fresh PostgreSQL
  database.

Commands (from `backend/`):

```bash
alembic upgrade head
alembic current
alembic revision --autogenerate -m "describe the change"
alembic downgrade -1
```

The Compose entrypoint runs `alembic upgrade head` on every container start, after
confirming PostgreSQL accepts a real connection.

Hosted deployments do not rely on that script. The app applies the chain itself from its
lifespan hook (`app/services/schema.py`) before serving traffic, because a managed host
starts the process with its own command and may never run the image ENTRYPOINT. The
failure that motivated it was silent: `SELECT 1` (and therefore `GET /health`) succeeded
against an empty database while every signup and login returned `500` — `relation "users"
does not exist`. The upgrade is a no-op at head, and a refused upgrade is logged with its
traceback without stopping the API, so `GET /health` can still report `services.schema`.

`AUTO_MIGRATE` (default `true`) controls that step and is deliberately independent of
`ENVIRONMENT`: an earlier version only migrated in `production`/`staging`, so a service
created outside `render.yaml` ran with the development default, never migrated, and every
login answered `500`. Set `AUTO_MIGRATE=false` where another process owns the schema
(`docker compose exec backend alembic upgrade head`, CI).

A database built by `Base.metadata.create_all` (what `scripts/seed.py` does) has the
tables but no `alembic_version` row, so it reports `not_initialised` and `alembic upgrade
head` will refuse to recreate existing tables. Create seeded data only in databases that
were migrated first, and set `AUTO_MIGRATE=false` for such a local database so startup
does not retry (and log a failure) on every restart.

---

## 5. Indexes

| Table | Index | Unique |
|-------|-------|--------|
| all tables | `ix_<table>_id` on `id` | no |
| `users` | `ix_users_email` | **yes** |
| `users` | `ix_users_store_id` | no |
| `password_reset_tokens` | `ix_password_reset_tokens_token_hash` | **yes** |
| `password_reset_tokens` | `ix_password_reset_tokens_user_id` | no |
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
  database dump. Back it up alongside the DB or report images will 404. On Render it lives
  on the mounted disk at `/var/data/uploads`.
- **Email credentials are in the database:** `email_settings` holds the verified transport
  secret. A database dump therefore contains that credential — treat dumps as secrets, and
  note the API never returns the key (only a masked fingerprint).
- **Timezone caveat:** SQLite stores naive datetimes; all API responses add `Z`, so
  consumers see correct instants. Direct SQL inspection will show naive values.
- **Engine creation is import-time:** changing `DATABASE_URL` requires a process restart.
- **Migrations are not run automatically outside Compose;** run `alembic upgrade head`
  before starting a manually managed deployment.
