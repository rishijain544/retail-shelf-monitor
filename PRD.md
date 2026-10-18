# Product Requirements Document (PRD)

**Product:** ShelfMonitor — Retail Shelf Monitoring & Restocking Assistant
**Repository:** `retail-shelf-monitor`
**Status:** Living document (describes the system as built, not an aspirational spec)
**Last reviewed:** 2026-10-18

---

## 1. Problem

Retail staff discover empty shelves by walking the aisles. That is slow, inconsistent,
and happens *after* a customer has already seen a gap. Photos of shelves already exist
(handheld captures, fixed cameras, phone snapshots), but nothing turns them into a
prioritised restocking action.

**ShelfMonitor** turns a shelf photo into:

1. a measured **fill percentage**,
2. a plain-language **assessment and recommended action**, and
3. a **high-urgency alert** delivered to Slack and to the email of every account that
   belongs to the store.

The dashboard then shows history, trends, and an alert feed so a store can see whether
shelf conditions are improving.

---

## 2. Goals

| # | Goal | How it is satisfied today |
|---|------|---------------------------|
| G1 | Turn a shelf photo into a quantified fill level | YOLOv8 empty-space detector + OpenCV heuristics → `fill_percentage` in `0..1` |
| G2 | Explain the number in actionable language | Anthropic Claude tool-use report (`summary`, `urgency`, `recommended_action`) with a deterministic rule-based fallback |
| G3 | Notify the right people without per-person setup | One installation-wide Slack webhook + one email transport; recipients resolved from `users` rows of the store that owns the camera |
| G4 | Give each store a private view | Every dashboard route and every tenant-scoped API query is filtered by the signed-in user's `store_id` |
| G5 | Survive unreliable inputs and providers | Image quality gate, low-confidence flag, graceful degradation when Slack/email/LLM are unconfigured or down |
| G6 | Be honest about uncertainty | A zero-detection capture is flagged `low_confidence` and never presented as a confident 100 % full shelf |

## 3. Non-goals (current scope)

- Live video ingestion / RTSP camera streaming. The product consumes **uploaded images**.
- Multi-user roles, invitations, or per-user permissions. One account per store today.
- Inventory, POS, ERP, or SKU-level replenishment integration.
- On-device / edge inference. Analysis runs server-side in the background task.
- Native mobile apps. The React dashboard is responsive web only.
- Billing, subscriptions, or usage metering.
- Multi-language UI (English only).

---

## 4. Users and personas

| Persona | Need | What they use |
|---------|------|---------------|
| **Store owner / manager** | Know which aisle needs restocking first, and receive alerts where they already look (Slack, email) | Dashboard, Alerts, Trends, Email setup |
| **Store associate** | Upload a shelf photo and get a clear instruction | Dashboard upload, report detail |
| **Operations / reviewer** | Verify the system is healthy and alerts are actually leaving the building | Health cards, `GET /health`, `POST /alerts/test` |
| **Developer / operator** | Run, migrate, seed, and configure the stack | README, [architecture.md](architecture.md), [database.md](database.md), [api.md](api.md), [AGENTS.md](AGENTS.md) |

Signup creates a **Store and its owner account in one transaction**, so a new user is
productive immediately and their data is isolated from other stores.

---

## 5. Core user journeys

### 5.1 First run
1. Visit `/signup`, enter store name, optional location, email, password (min 8 chars).
2. A `stores` row and a `users` row are created together; a JWT is returned and the user
   lands on the dashboard.
3. If cameras do not exist yet, open **Directory** (`/admin`) and add a camera
   (aisle + name). The camera is always created in the caller's store.

### 5.2 Capture → assessment
1. On the dashboard, choose a camera and upload a JPG/PNG/BMP/WEBP shelf photo.
2. The API answers `202` immediately with a receipt (`capture_id`, `report_url`,
   `status: "processing"`).
3. The pipeline runs in the background: quality gate → detection → fill calculation →
   LLM report → persistence → alerting.
4. The UI polls the report every 1.5 s (120 s timeout) and renders fill %, urgency,
   summary, recommended action, detections, and the previous fill for trend context.
5. If the pipeline fails, the capture is marked `failed` with a human-readable
   `error_message` (for example a blurry/irrelevant photo) instead of hanging forever.

### 5.3 Alerting
1. A report with `urgency = "high"` **and** `low_confidence = false` raises one alert row.
2. Slack is posted first (if `SLACK_WEBHOOK_URL` is set).
3. Email is sent to the login address of every account in the store that owns the
   camera (if an email transport is configured), with `ALERT_EMAIL_TO` as a global
   override when set.
4. The `alerts` row records `sent_to_slack`, `sent_to_email`, `email_recipients`, and
   `sent_at` (only when Slack succeeded).
5. A store owner can prove the wiring with **Send test alert**, which mails their own
   login address and posts to Slack through the same code path.

### 5.4 Reviewing history
- **Dashboard** lists recent reports with filters, CSV export, and a per-report detail page.
- **Alerts** lists the alert feed (newest first) and exposes email configuration.
- **Trends** charts fill % over time per aisle (Recharts, lazily loaded) and offers a
  table view so colour is never the only encoding.
- **Directory** lists stores and the store's cameras and adds cameras.

---

## 6. Functional requirements

### 6.1 Accounts and access

| ID | Requirement |
|----|-------------|
| FR-A1 | `POST /api/v1/auth/signup` creates a store + owner atomically and returns a JWT, `expires_in`, and the user. |
| FR-A2 | Duplicate email returns `409`; emails are trimmed and lower-cased before lookup/insert. |
| FR-A3 | `POST /api/v1/auth/login` returns `401` for an unknown email or wrong password, with the same message and comparable timing (dummy-hash verification prevents user enumeration). |
| FR-A4 | `GET /api/v1/auth/me` validates the bearer token and returns `{id, email, store_id}`. |
| FR-A5 | Passwords are stored only as bcrypt hashes (max 72 bytes, truncated consistently). |
| FR-A6 | Access tokens are HS256 JWTs with `sub = user id`, `iat`, `exp` (default 1440 minutes). |
| FR-A7 | In `production`/`staging`, a missing or `< 32`-character `SECRET_KEY` aborts startup. |

### 6.2 Capture ingestion

| ID | Requirement |
|----|-------------|
| FR-C1 | `POST /api/v1/captures/upload` accepts multipart `camera_id`, `file`, optional `conf` (default `0.25`) and `iou` (default `0.5`). |
| FR-C2 | Non-image content types and empty files are rejected with `400`. |
| FR-C3 | The camera must belong to the caller's store (`403` otherwise, `404` when unknown). |
| FR-C4 | The file is stored under a UUID name in `UPLOAD_DIR` and served at `/uploads/<file>`; the capture row is committed before background work starts. |
| FR-C5 | The response is `202` with `capture_id`, `status: "processing"`, `image_url`, `captured_at`, `report_url`. |
| FR-C6 | Analysis must not block the HTTP response. |

### 6.3 Analysis and reporting

| ID | Requirement |
|----|-------------|
| FR-R1 | The analyzer rejects unusable photos (too dark, blurry, too small, not shelf-like) with a user-facing message recorded on the capture. |
| FR-R2 | `fill_percentage` is a fraction in `0..1` computed from the union area of empty regions, or pixel-level coverage when the heuristic detector wins. |
| FR-R3 | Zero detections on an unassessed result sets `low_confidence = true`; such rows are excluded from alerting and from trend baselines. |
| FR-R4 | The LLM report is structured (`summary`, `urgency ∈ {low, medium, high}`, `recommended_action`) and falls back to a deterministic rule-based report when the Anthropic API is unavailable. |
| FR-R5 | Urgency bands: `low` ≥ 60 % fill, `medium` 30–60 %, `high` < 30 %. |
| FR-R6 | `GET /api/v1/captures/{id}/report` returns `processing`, `completed`, or `failed`, with detections and `previous_fill` when completed. |
| FR-R7 | `GET /api/v1/reports` supports `aisle`, `urgency`, `start_date`, `end_date`, `limit` (1–200, default 50), `offset`, scoped to the caller's store, newest first. |
| FR-R8 | `GET /api/v1/reports/export` returns the complete filtered set as CSV with capture context. |
| FR-R9 | `GET /api/v1/reports/{id}` returns `404` for a report outside the caller's store. |

### 6.4 Alerting

| ID | Requirement |
|----|-------------|
| FR-AL1 | One alert row is created per high-urgency, confident shelf report. |
| FR-AL2 | Slack delivery uses `SLACK_WEBHOOK_URL`; unconfigured or failing Slack never fails the pipeline. |
| FR-AL3 | Email delivery uses the installation-wide transport: SMTP relay first, provider API key second; with neither, the send is skipped and logged. |
| FR-AL4 | Email recipients are every `users.email` in the store that owns the camera, resolved at send time; `ALERT_EMAIL_TO` overrides the list when set. |
| FR-AL5 | `GET /api/v1/alerts` returns a store-scoped page with report/camera/store context and delivery flags. |
| FR-AL6 | `GET /api/v1/alerts/email-config` reports `configured`, `transport`, `from_address`, `last_error`, `alert_email_to`, and SMTP host/user. |
| FR-AL7 | `POST /api/v1/alerts/email-config` and `POST /api/v1/alerts/smtp-config` verify credentials with a **real test send** before persisting them to `backend/.env`; a failure restores the previous settings and returns `400`. |
| FR-AL8 | `POST /api/v1/alerts/test` mails the caller's own login address and posts to Slack; `503` when nothing is configured, `502` when every configured channel fails. |

### 6.5 Directory and health

| ID | Requirement |
|----|-------------|
| FR-D1 | `GET /api/v1/stores` lists all stores (reference data); `POST /api/v1/stores` creates one. |
| FR-D2 | `GET /api/v1/cameras` returns only the caller's store's cameras; `POST /api/v1/cameras` always uses the caller's `store_id`. |
| FR-D3 | `GET /api/v1/health` always answers `200` and reports `api`, `database`, `alert_slack`, `alert_email`, and `alert_email_transport`. |
| FR-D4 | A failed email configuration or delivery is visible as `unhealthy: <reason>`, not hidden behind a green badge. |

---

## 7. UX requirements

- The shell (header, sidebar, health cards) is persistent across authenticated routes.
- Login and signup are standalone pages with no sidebar or health cards.
- Health is polled every 10 s with an 8 s request timeout and a manual refresh.
- Upload progress and pipeline state are explicit: uploading → analysing → result/failure.
- `low_confidence` reports are labelled **"Unable to assess"**; they must never render as
  a green 100 % fill card.
- Numbers are localised: timestamps arrive as UTC (`…Z`) and are rendered in the viewer's
  timezone.
- Colour is never the sole signal on the Trends chart; a table view is always available.
- Empty states explain the next action (add a camera, upload a capture, configure email).

---

## 8. Non-functional requirements

| Area | Requirement |
|------|-------------|
| **Isolation** | No endpoint may return another store's captures, reports, alerts, or cameras. `store_id` is derived from the token, never from the request body. |
| **Resilience** | Slack, email, LLM, and detection failures downgrade the feature, not the request. The pipeline always terminates a capture in `ready` or `failed`. |
| **Honesty** | Configuration state is reported with reasons (`last_error`, `config_error`) rather than optimistic booleans. |
| **Secrets** | No credential is committed. `backend/.env` is git-ignored; API keys are masked in responses; `.env` writes are targeted and preserve the rest of the file. |
| **Portability** | Runs on SQLite locally and PostgreSQL 15 in Compose. Timestamps are stored UTC and serialised with an explicit `Z`. |
| **Observability** | Structured log lines at pipeline boundaries; health probe for API, DB, Slack, email. |
| **Performance** | Upload responds before analysis; listings use `joinedload` to avoid N+1 queries; page size capped at 200. |

---

## 9. Success metrics

These are the signals the product is judged on (instrumentation is not yet built; the
values below are targets, not measurements):

1. **Capture completion rate** — share of uploads reaching `completed` (not `failed`).
2. **Time to assessment** — upload accepted → report available.
3. **Alert delivery rate** — alerts with `sent_to_slack` or `sent_to_email = true`.
4. **Actionability** — share of high-urgency reports where staff mark restocking complete
   (requires a future acknowledgement feature).
5. **Trend improvement** — fill % per aisle over rolling 7 days.

## 10. Assumptions and constraints

- Each signup creates a store and its owner account, so the normal flow is one account
  per store. The schema does not forbid additional accounts in the same store, and alert
  delivery deliberately emails every account it finds there.
- Photos are provided by the user; lighting and framing are the user's responsibility, but
  the quality gate rejects the worst cases.
- An SMTP relay (e.g. a Gmail App Password) is the recommended email transport because it
  can reach **any** login address. A Resend free-tier key can only mail the Resend
  account's own address unless a domain is verified.
- The detector is a custom YOLOv8 checkpoint (`ml/weights/empty_space_best.pt`); a stock
  COCO model is explicitly refused at load time.
- The Anthropic model is `claude-3-5-haiku-20241022` with tool-forced structured output.

## 11. Known limitations

- `CORS` currently allows `*` origins with credentials — acceptable for local development,
  must be narrowed before a public deployment.
- JWTs are stored in `localStorage`, which is readable by any XSS on the origin;
  httpOnly cookies are the recommended production upgrade.
- `GET /stores` returns every store (directory data), not just the caller's store.
- `products` table exists in the schema but no API or pipeline reads or writes it.
- No per-user alert preferences: every account in a store receives high-urgency alerts.
- No acknowledgement/"restocked" workflow, so a gap can alert once per capture.
- Background work runs in-process (`FastAPI BackgroundTasks`); a restart mid-pipeline
  leaves that capture at `processing` until it is retried manually.

## 12. Roadmap candidates

1. Camera-level schedules / automatic periodic capture.
2. Alert acknowledgement and de-duplication window per camera.
3. Per-user notification preferences (mute, digest, channel).
4. Role-based access (owner / staff / viewer).
5. SKU-level recognition and planogram comparison.
6. Replace `BackgroundTasks` with a durable queue (Celery/RQ) and retries.
7. HTTP-only cookie sessions and CSRF protection.

## 13. Demo data

`backend/scripts/seed.py` creates two stores, five cameras, sample captures with generated
images, reports, alerts, and one demo account. Credentials are documented in
[README.md](README.md#demo-login) (`demo@shelfmonitor.test` / `demo-2026-shelf`) and are
local-development only.

## 14. Glossary

| Term | Meaning |
|------|---------|
| **Capture** | One uploaded shelf image tied to a camera. |
| **Detection** | One detected empty-space bounding box (`class = "empty"`). |
| **Shelf report** | The analysis result for a capture: fill %, urgency, summary, action, confidence flag. |
| **Alert** | Delivery record for a high-urgency report (Slack + email). |
| **Low confidence** | The analyzer could not produce a trustworthy assessment; fill/urgency must not be treated as measurements. |
| **Transport** | The configured email path: `smtp`, `api` (provider key), or `none`. |
| **Store scope** | The `store_id` from the authenticated user that bounds every query. |
