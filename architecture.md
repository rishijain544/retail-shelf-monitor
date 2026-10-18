# Architecture

**System:** ShelfMonitor — Retail Shelf Monitoring & Restocking Assistant
**Scope:** How the repository is organised, how a request flows end to end, and which
constraints shape the design. This document describes the code as it exists today.

Related: [PRD.md](PRD.md) · [database.md](database.md) · [api.md](api.md) · [AGENTS.md](AGENTS.md)

---

## 1. System at a glance

```
┌─────────────────────────────┐        ┌──────────────────────────────────────┐
│  Browser (React 18 + TS)    │        │  FastAPI backend (Python 3.10)       │
│  Vite dev server :5173      │ HTTPS  │  uvicorn :8000                       │
│  ─────────────────────      │ ─────► │  ──────────────────────────────      │
│  pages/ + hooks/ + api/     │  JSON  │  api/v1/endpoints/*                  │
│  localStorage JWT           │        │  services/*  (analysis, LLM, alerts) │
└─────────────────────────────┘        │  models/*    (SQLAlchemy ORM)        │
                                       └───────┬───────────────┬──────────────┘
                                               │               │
                     ┌─────────────────────────▼──┐   ┌────────▼─────────────────┐
                     │ SQLite (local dev)         │   │ External providers       │
                     │ PostgreSQL 15 (Compose)    │   │  · Anthropic Claude      │
                     └────────────────────────────┘   │  · Slack webhook         │
                                                      │  · SMTP relay / Resend   │
                     ┌────────────────────────────┐   └──────────────────────────┘
                     │ ml/ YOLOv8 + OpenCV        │
                     │ imported in-process by     │
                     │ analyzer_service           │
                     └────────────────────────────┘
```

Three deployable units in the monorepo:

| Unit | Tech | Entry point |
|------|------|-------------|
| `backend/` | FastAPI, SQLAlchemy, Alembic | `app.main:app` |
| `frontend/` | React 18, TypeScript, Vite, Tailwind | `src/main.tsx` → `src/App.tsx` |
| `ml/` | Ultralytics YOLOv8, OpenCV, NumPy | `ShelfAnalyzer` in `ml/shelf_analyzer.py` |

`ml/` is **not** a separate service. The backend imports it in-process through
`app/services/analyzer_service.py`.

---

## 2. Repository layout

```
retail-shelf-monitor/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI app, CORS, /uploads mount
│   │   ├── api/v1/
│   │   │   ├── api.py              router composition
│   │   │   └── endpoints/          health, auth, captures, reports, alerts,
│   │   │                           stores, cameras
│   │   ├── core/
│   │   │   ├── config.py           pydantic-settings, env parsing, strict checks
│   │   │   ├── database.py         engine, SessionLocal, Base, get_db
│   │   │   ├── security.py         bcrypt hashing, JWT encode/decode
│   │   │   └── auth_deps.py        get_current_user, store/camera guards
│   │   ├── models/                 stores, cameras, captures, detections,
│   │   │                           shelf_reports, alerts, users, products,
│   │   │                           email_settings
│   │   ├── schemas/                Pydantic request/response models
│   │   └── services/
│   │       ├── analyzer_service.py ML adapter, singleton + inference lock
│   │       ├── capture_pipeline.py background orchestration
│   │       ├── llm_service.py      Anthropic tool use + rule fallback
│   │       ├── slack_service.py    incoming webhook
│   │       ├── email_service.py    SMTP / provider-API delivery
│   │       ├── email_setup.py      verify credentials with a real test send
│   │       └── email_settings.py   load/save that transport in the database
│   ├── migrations/versions/        Alembic chain (7 revisions)
│   ├── scripts/                    seed.py, set_resend_key.py
│   ├── tests/                      pytest suite
│   ├── Dockerfile                  repo-root build context (needs ml/)
│   └── docker-entrypoint.sh        wait for PG → alembic upgrade → uvicorn
├── frontend/
│   ├── src/
│   │   ├── App.tsx                 route table + AuthGate
│   │   ├── components/             AppLayout, HealthCards
│   │   ├── hooks/                  useAuth, useHealth
│   │   ├── api/                    client.ts (typed transport), errors.ts
│   │   └── pages/                  Dashboard, ShelfDetail, Alerts, Trends,
│   │                               Admin, LoginPage, SignupPage
│   ├── nginx.conf                  SPA fallback + asset caching (prod image)
│   └── Dockerfile                  deps → build → nginx runtime
├── ml/
│   ├── shelf_analyzer.py           quality gate, detection, fill %, CLI
│   ├── scripts/                    preprocess, detect, infer, train
│   └── tests/                      analyzer unit tests
├── docker-compose.yml              db + backend + frontend
├── render.yaml                     API blueprint (Docker, persistent uploads disk)
└── README.md
```

---

## 3. Backend architecture

### 3.1 Layering

```
HTTP request
   │
   ▼
endpoints/*.py           validate input, enforce auth/tenant scope, shape response
   │                     (FastAPI dependencies: get_current_user, get_db)
   ▼
services/*.py            domain work: analysis, LLM, Slack, email, credential storage
   │
   ▼
models/*.py              SQLAlchemy declarative models (Base from core/database)
   │
   ▼
core/database.py         Engine + SessionLocal; SQLite or PostgreSQL
```

Rules the code follows:

- **Endpoints never trust the client for scope.** `camera_id` is verified against the
  caller's store via `_assert_camera_belongs_to_user`; `store_id` is always taken from
  `current_user`.
- **Services never raise for delivery problems.** `slack_service` and `email_service`
  return `False` / `[]` and log; the pipeline records the outcome on the alert row.
- **Serialisation lives in schemas.** Context joins (store/aisle/camera) are attached in
  endpoint helpers (`_with_context`) with `joinedload` to avoid N+1 queries.
- **Errors are JSON.** A middleware wraps unhandled exceptions into
  `{"detail": "Internal server error"}` and is registered *before* CORS so the response
  still carries CORS headers.

### 3.2 Router composition

`app/api/v1/api.py` mounts everything under `settings.API_V1_STR` (`/api/v1`):

| Prefix | Router | Auth |
|--------|--------|------|
| `/health` | `health.py` | public |
| `/auth` | `auth.py` | signup/login public, `/me` bearer |
| `/captures` | `captures.py` | bearer |
| `/reports` | `reports.py` | bearer |
| `/alerts` | `alerts.py` | bearer |
| `/stores` | `stores.py` | bearer |
| `/cameras` | `cameras.py` | bearer |

Static uploads are mounted **outside** the API prefix: `app.mount("/uploads", …)`.

### 3.3 Authentication and tenancy

- `POST /auth/signup` inserts `Store` + `User` in one transaction (`flush` → assign
  `store_id` → `commit`); an `IntegrityError` rolls both back.
- Passwords: bcrypt via passlib; input truncated to 72 bytes consistently on hash and
  verify.
- Tokens: HS256 JWT, `sub` = user id string, `exp` from
  `ACCESS_TOKEN_EXPIRE_MINUTES` (default 24 h). `SECRET_KEY` is required in
  production/staging; in development an ephemeral random key is generated (tokens die on
  restart, with a warning).
- `get_current_user` decodes the bearer token and loads the `User` row, returning `401`
  with `WWW-Authenticate: Bearer` for every failure mode.
- `get_current_active_store` is defined for store-scoped dependencies and returns the
  user's store or `403`; the current endpoints call `_assert_camera_belongs_to_user`
  directly when they need a camera boundary.

Tenant boundary:

```
User.store_id ──► Camera.store_id ──► Capture.camera_id ──► ShelfReport.capture_id ──► Alert.shelf_report_id
```

Every capture, report, alert, and camera endpoint joins back to
`Camera.store_id == current_user.store_id` (the store directory is shared reference data
and is intentionally not scoped).

### 3.4 Capture pipeline

```
POST /captures/upload
  ├─ validate content-type, camera ownership, non-empty body
  ├─ write UUID file to UPLOAD_DIR
  ├─ INSERT captures (status="processing"), COMMIT
  ├─ BackgroundTasks.add_task(process_capture, capture_id, conf, iou)   → returns 202
  │
  └─ threadpool thread: process_capture()
       1. open its own SessionLocal (the request session is gone)
       2. analyzer_service.analyze(path, conf, iou)
            └─ ml.shelf_analyzer.ShelfAnalyzer.analyze_shelf()
                 quality gate → deskew → (upscale) → YOLOv8 → NMS
                 → heuristic fallback / coverage arbitration → fill %
       3. INSERT detections
       4. read previous confident report for the same camera (capture.id < this id)
       5. llm_service.generate_shelf_report(...)  → Anthropic or rule fallback
       6. INSERT shelf_reports (low_confidence flag when unassessed + 0 detections)
       7. if urgency == "high" and not low_confidence:
            slack_service.notify_high_urgency(...)
            recipients = SELECT email FROM users WHERE store_id = camera.store_id
            email_service.notify_high_urgency(...)
            INSERT alerts(sent_to_slack, sent_to_email, email_recipients, sent_at)
       8. capture.status = "ready"; COMMIT
     on exception: rollback → capture.status = "failed", error_message → COMMIT
```

Why `id <` rather than `captured_at` for "previous": timestamps can tie and pipelines can
finish out of order; the monotonic id is stable.

**Status vocabulary**

| Layer | Values |
|-------|--------|
| DB `captures.status` | `processing`, `ready`, `failed` |
| API `status` | `processing`, `completed` (mapped from `ready`), `failed` |

### 3.5 Detection stage (`ml/shelf_analyzer.py`)

1. **Quality gate** — rejects unreadable, too-small, blurry, too-dark, or irrelevant
   images. Raises `ImageQualityError` with a user-facing message that travels into
   `capture.error_message`.
2. **Deskew** — geometric correction only; no photometric "enhancement" on the inference
   path (it previously pushed inputs out of the training distribution).
3. **YOLOv8** — custom checkpoint, `conf=0.25`, `iou=0.5`, at training-like scale
   (small images are upscaled to 640 and boxes mapped back).
4. **IoU-only NMS** — no second confidence cut.
5. **Fallback / arbitration** — if YOLO finds nothing, classical OpenCV cues decide
   product vs empty space (saturation, edges, floor/fixture exclusion). If YOLO finds a
   little but the cues say the scene is mostly empty and model coverage is low, the
   pixel-level measurement wins.
6. **Fill** — `1 − (union area of empty boxes / image area)` for the YOLO path, or the
   heuristic product-coverage figure for the heuristic path; clipped to `0..1`.
7. **Return contract** — `detections`, `fill_percentage`, `empty_regions`,
   `skew_angle`, `detector` (`yolo` | `heuristic`), `assessed`, `detection_count`.

`analyzer_service` loads the model once (lazy singleton) and serialises inference with a
lock because background tasks run concurrently on the threadpool.

### 3.6 LLM stage

- Anthropic client, model `claude-3-5-haiku-20241022`, 512 max tokens.
- Tool `generate_report` with `tool_choice` forced, so the reply is always structured
  JSON matching `ShelfReportOutput`.
- Prompt includes fill %, aisle, empty-region count, and trend vs `previous_fill`.
- On missing key, timeout, or API error, a deterministic rule-based report is produced
  from the same thresholds.

### 3.7 Alerting

**Slack** — one HTTPS POST to `SLACK_WEBHOOK_URL`; success requires HTTP 200 and body
`ok`; 10 s timeout.

**Email** — two transports, chosen in this order:

1. **SMTP relay** (`SMTP_HOST` + `SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`), STARTTLS on
   587 or implicit TLS on 465. Can deliver to *any* address.
2. **Provider API** (`RESEND_API_KEY`), one HTTPS POST to `EMAIL_API_URL`. A free-tier
   key can only mail the account's own address unless a domain is verified.

`ALERT_EMAIL_TO` (comma-separated) overrides recipients globally; `ALERT_EMAIL_ENABLED=false`
silences email while leaving Slack intact. Failures record `_last_error`, surfaced through
`config_error()` / `last_error()` in health and email-config responses.

**Credential installation** (`email_setup.install` / `install_smtp`, then
`email_settings.save_*`):

```
apply values in memory
  → send a real test message to the caller's login address
  → on failure: restore previous settings, raise ValueError (HTTP 400)
  → on success: save the singleton email_settings row, retire the other transport
```

This "prove, then persist" flow means a bad paste can never silently break alerts.

Persistence moved from `backend/.env` to the database because a deployed container has a
read-only (Render) or ephemeral (Vercel, `/tmp`) filesystem: the old upsert only worked on
a developer machine, so a verified key vanished at the next redeploy and alerts silently
recorded `sent_to_email=false`. At startup the lifespan hook applies the saved row over the
environment variables, so env-var configuration still works for operators who never open
the setup screen.

### 3.8 Configuration

`app/core/config.py` (pydantic-settings) reads `backend/.env`:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Explicit SQLAlchemy URL (overrides Postgres parts) |
| `POSTGRES_SERVER/USER/PASSWORD/DB/PORT` | Postgres connection parts |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` | LLM report generation |
| `UPLOAD_DIR` | Image storage (default `backend/uploads`) |
| `SLACK_WEBHOOK_URL` | Slack alerts |
| `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_API_URL` | API email transport |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM/USE_TLS/USE_SSL` | SMTP transport |
| `ALERT_EMAIL_TO`, `ALERT_EMAIL_ENABLED` | Recipient override / master switch |
| `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` | Browser origins allowed to call the API |
| `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT signing and lifetime |
| `ENVIRONMENT` | `development` \| `production` \| `staging` |

Credentials are stripped of quotes/whitespace on load; strict environments refuse to
start without a strong `SECRET_KEY`.

### 3.9 Python dependencies

`fastapi`, `uvicorn[standard]`, `sqlalchemy`, `alembic`, `pydantic-settings`,
`psycopg2-binary`, `python-dotenv`, `httpx`, `anthropic`, `python-multipart`, `passlib`,
`bcrypt` pinned `<4.1` (passlib 1.7.4 cannot load newer bcrypt), `PyJWT`.

---

## 4. Frontend architecture

### 4.1 Shape

- **Routing** — `react-router-dom` v6. Standalone `/login` and `/signup`; everything else
  is nested under `<AuthGate><AppLayout/></AuthGate>`.
- **Lazy loading** — `Trends` is `React.lazy` so Recharts stays out of the Dashboard
  bundle.
- **Auth state** — `useAuth` keeps a single source of truth in
  `localStorage["sm:auth:state"]` and notifies module-level listeners, so hook instances
  and the transport layer stay in sync. `AuthGate` calls `GET /auth/me` on mount to
  validate the persisted token.
- **HTTP** — one typed client (`src/api/client.ts`): `request()` prefixes
  `API_BASE_URL` (default `http://localhost:8000/api/v1`), injects
  `Authorization: Bearer <token>` from storage, throws `ApiError(status, detail)` for
  non-2xx, and leaves multipart `Content-Type` to the browser.
- **Errors** — `src/api/errors.ts` provides `describeError` / `isAbortError` for
  consistent user-facing messages and abort handling.
- **Styling** — Tailwind CSS utility classes; icons from `lucide-react`; charts from
  `recharts`.
- **No global store** — each page owns its data with `useState`/`useEffect`,
  `AbortController` cleanup, and explicit loading/error states.

### 4.2 Pages

| Route | Page | Responsibilities |
|-------|------|------------------|
| `/login`, `/signup` | `LoginPage`, `SignupPage` | Credentials, one-click demo login, redirect to dashboard |
| `/` | `Dashboard` | Upload capture, poll pipeline, list/filter reports, CSV export |
| `/reports/:id` | `ShelfDetail` | Image, detections overlay, fill/urgency/summary/action, previous fill |
| `/alerts` | `Alerts` | Alert feed, test alert, email/SMTP configuration dialog |
| `/trends` | `Trends` | Fill % over time per aisle (line chart + accessible table) |
| `/admin` | `Admin` | Store/camera directory, add camera |

`AppLayout` hosts the header (store name, signed-in account, logout), sidebar navigation,
and `HealthCards`. `useHealth` polls `/health` every 10 s with an 8 s timeout.

### 4.3 Frontend build/deploy

- Dev: `vite --host 0.0.0.0` on `:5173`, proxying `/uploads` to `localhost:8000`.
- Prod image: multi-stage `npm ci` → `npm run build` → `nginx:1.27-alpine`, with SPA
  fallback, immutable caching for hashed `/assets/*`, and no caching for `index.html`.
- `VITE_API_URL` is a **build-time** value (Vite inlines it); changing it requires a
  rebuild.
- On Vercel the project's Root Directory is `frontend/`; `frontend/vercel.json` rewrites
  every path to `index.html` for client-side routes and adds `nosniff` /
  `strict-origin-when-cross-origin` headers. No dev server or nginx runs there.

---

## 5. Data and persistence

See [database.md](database.md) for the full schema. Key architectural points:

- Two supported dialects: SQLite (file, default local) and PostgreSQL 15 (Compose).
  Models use Python-side defaults for timestamps so the ORM never emits dialect-specific
  `now()`.
- SQLite drops timezone info, so response schemas serialise every timestamp through
  `as_utc_iso()` (`…Z`), and the browser converts to local time.
- Cascades: deleting a store removes its cameras and users; deleting a camera removes its
  captures; captures remove detections and reports; reports remove alerts.
- `fill_percentage` is a **fraction** (`0..1`), not a percentage. UI multiplies by 100.

---

## 6. Deployment

### 6.1 Docker Compose (recommended)

```
db (postgres:15-alpine, healthcheck pg_isready)
   │ service_healthy
backend (python:3.10-slim, entrypoint waits on psycopg2 → alembic upgrade head → uvicorn)
   │ service_started
frontend (nginx serving the built SPA, published on :5173)
```

- Backend build context is the **repo root** so `ml/` and its weights land at `/ml`
  (matching `analyzer_service`'s `parents[3]` resolution).
- Compose mounts `./backend` into `/app` for live reload, with anonymous volumes for
  `.venv`/`__pycache__`.
- Frontend is a static nginx image; the container's `:80` is published as `:5173`.
- `VITE_API_URL` is passed as a build arg, not a runtime env var.
- The backend healthcheck greps the JSON body for `"status": "healthy"` because
  `/health` always answers `200`.

### 6.2 Local manual setup

- `alembic upgrade head`, then `uvicorn app.main:app --reload --port 8000`.
- `npm install && npm run dev` in `frontend/`.
- SQLite is used when `DATABASE_URL` points at a file (the default local database is
  `backend/shelf_monitoring.db`).

### 6.3 Environment separation

`ENVIRONMENT` gates strict configuration checks only. There is no separate staging
deployment definition in the repository.

### 6.4 Vercel frontend + Render API (hosted split)

```
Vercel (static SPA)  ──HTTPS/JSON──►  Render web service (Docker)
  frontend/vercel.json                  backend/Dockerfile (repo-root context)
  VITE_API_URL baked at build           entrypoint: wait → alembic upgrade head → uvicorn
                                              │                    │
                                              ▼                    ▼
                                        Neon Postgres        /var/data (disk)
                                        (DATABASE_URL)       uploads served at /uploads
```

The split is forced by the analysis requirements, not preference: the Python function
limit on Vercel is 500 MB against a ~1.5 GB torch/ultralytics install, the deployed
filesystem is read-only, the request body is capped at 4.5 MB, and there is no guaranteed
work after the response — while this pipeline runs inference, writes an image, and sends
alerts after returning `202`.

Configuration that makes it work:

- `CORS_ORIGINS` must contain the exact Vercel origin; `CORS_ORIGIN_REGEX` can cover
  per-branch preview hostnames.
- `UPLOAD_DIR=/var/data/uploads` on the mounted Render disk, so images survive redeploys.
- `DATABASE_URL` points at Neon; the entrypoint prefers it over the Compose `POSTGRES_*`
  parts (it would otherwise wait on a host that does not exist).
- The Render service is a single instance: background tasks and the upload share a process.
- Uploads are still served by the API's `/uploads` mount, so the frontend's relative image
  URLs keep working unchanged.

---

## 7. Cross-cutting concerns

| Concern | Implementation |
|---------|----------------|
| **Auth** | JWT bearer; `get_current_user` dependency on every private route |
| **Tenant isolation** | `store_id` from the token; camera ownership asserted before reads/uploads |
| **Error shape** | FastAPI `{"detail": …}`; unhandled → JSON 500 via middleware |
| **Timestamps** | Stored UTC, serialised with `Z`, rendered locally |
| **Secrets** | `.env` git-ignored; keys masked (`re_abc…wxyz`); verified email credentials stored in the DB, never returned |
| **Graceful degradation** | Slack/email/LLM failures never abort a capture |
| **Confidence honesty** | `low_confidence` flag excluded from alerts and trend baselines |
| **Logging** | `logging` at every pipeline boundary with capture ids and outcomes |
| **Static files** | `/uploads` mounted from `UPLOAD_DIR`, created at import time |

---

## 8. Testing strategy

| Layer | Runner | Location | Focus |
|-------|--------|----------|-------|
| Backend | `pytest` | `backend/tests/` | Auth, upload/report flow, tenant isolation, read filters/pagination, email/Slack services, health, timestamps, e2e capture flow |
| ML | `pytest` | `ml/tests/` | Analyzer geometry, NMS, fill math, quality gate |
| Frontend | `vitest` + Testing Library (`jsdom`) | `frontend/src/**/__tests__/` | Pages, auth flow, error rendering, alerts/email dialog, charts |

`backend/tests/conftest.py` has an autouse fixture that blanks every email credential, so
a developer's real `.env` can never send mail during a test run. Slack is mocked at the
`httpx` boundary.

There is no CI workflow committed in the repository; checks are run locally.

---

## 9. Design decisions and trade-offs

| Decision | Rationale | Trade-off |
|----------|-----------|-----------|
| Background work via `FastAPI BackgroundTasks` | No broker required; simple local setup | In-process, no retries or durability across restarts |
| Custom YOLO checkpoint, no COCO fallback | Stock models classify people/vases as shelf gaps | App fails loudly if weights are missing |
| Heuristic + arbitration layer | Fine-tuned model generalises poorly to arbitrary photos | More tuning constants to maintain |
| LLM tool-forced JSON with rule fallback | Reliable schema; still works without a key | Summary quality drops to template text without Anthropic |
| SMTP before API transport | SMTP can reach any login address; free-tier keys cannot | Requires an app password from the operator |
| Verify-then-persist credentials | A typo cannot silently disable alerting | Configuration performs a real send |
| JWT in localStorage | Simple, works with the static SPA | XSS-exposed; cookies would be stronger |
| SQLite + PostgreSQL dual support | Zero-setup local dev, real DB in Compose | Dialect quirks (naive datetimes) require serializer care |
| `low_confidence` instead of nullable fill | Every consumer keeps a clean float | Clients must honour the flag |
| Email credentials in the database | Survives redeploys on a read-only filesystem; the setup screen keeps working | One more table to migrate; the secret lives in the DB |
| API on Render, SPA on Vercel | Keeps YOLO, disk uploads and background delivery working | Two hosts and two dashboards to configure |

---

## 10. Extension points

- **New notification channel** — add a `services/*_service.py` returning `bool`, call it
  from `capture_pipeline`, add a column/flag to `alerts`.
- **Durable queue** — `process_capture(capture_id, conf, iou)` is a plain sync function,
  written to be wrappable as a Celery task.
- **Per-user preferences** — `users` is already the recipient source; a preferences table
  joined at send time would be the natural addition.
- **New analysis backend** — implement the return contract from `analyze_shelf` and swap
  it inside `analyzer_service.analyze`.
