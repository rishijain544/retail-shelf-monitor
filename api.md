# API Reference

**Base URL:** `http://localhost:8000/api/v1`
**Interactive docs:** `http://localhost:8000/docs` · OpenAPI JSON: `/api/v1/openapi.json`
**Content type:** `application/json` unless noted; uploads use `multipart/form-data`

Related: [architecture.md](architecture.md) · [database.md](database.md)

---

## 1. Conventions

### 1.1 Authentication

Private endpoints require a bearer JWT:

```http
Authorization: Bearer <access_token>
```

Obtain a token from `POST /auth/signup` or `POST /auth/login`. Tokens are HS256 JWTs with
`sub = user id` and expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 1440 minutes = 24 h).

Missing, malformed, or expired tokens return:

```json
{ "detail": "Not authenticated." }
```

with `401` and `WWW-Authenticate: Bearer`.

### 1.2 Tenant scope

`store_id` is **never** accepted from the client. It is read from the authenticated user,
and every capture/report/alert/camera query is filtered to that store. Supplying a
`store_id` query parameter to `GET /reports` has no effect.

### 1.3 Errors

FastAPI's standard shape:

```json
{ "detail": "Human-readable explanation" }
```

| Status | Meaning |
|--------|---------|
| `400` | Invalid upload, malformed credentials, or rejected SMTP/API key |
| `401` | Missing/invalid token, or bad login credentials |
| `403` | Camera ownership check failed (e.g. the camera belongs to another store) |
| `404` | Unknown id, or a report outside your store |
| `409` | Email already registered |
| `422` | Request body/query validation failed (Pydantic) |
| `500` | Unhandled server error (`{"detail": "Internal server error"}`) |
| `502` | Alert channels configured but every one failed |
| `503` | No alert channel configured |

### 1.4 Pagination

List endpoints return a generic envelope (not a bare array):

```json
{
  "items": [ … ],
  "total": 128,
  "limit": 50,
  "offset": 0
}
```

- `limit` is capped at **200**, default **50**.
- `total` counts all rows matching the filters, independent of paging.
- `GET /stores` and `GET /cameras` are the exception: they return bare arrays.

### 1.5 Timestamps and numbers

- Timestamps are ISO-8601 **UTC with an explicit `Z`**, e.g. `"2026-10-18T09:29:52Z"`.
  Render them in the viewer's timezone.
- `fill_percentage` on reports is a **fraction `0..1`**, not `0..100`.
- `urgency` is `"low" | "medium" | "high"`.
- `low_confidence: true` means the fill/urgency pair is **not a measurement**; present the
  report as "unable to assess".

---

## 2. Endpoint index

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/` *(server root)* | — | API banner and doc links |
| GET | `/health` | — | API + database + alert-channel health |
| POST | `/auth/signup` | — | Create store + owner, return token |
| POST | `/auth/login` | — | Email/password login |
| GET | `/auth/me` | ✅ | Current account |
| POST | `/captures/upload` | ✅ | Upload shelf image, start analysis |
| GET | `/captures/{capture_id}/report` | ✅ | Poll analysis result |
| GET | `/reports` | ✅ | Paginated, filterable reports |
| GET | `/reports/export` | ✅ | CSV export of matching reports |
| GET | `/reports/{report_id}` | ✅ | One report |
| GET | `/alerts` | ✅ | Paginated alert feed |
| GET | `/alerts/email-config` | ✅ | Alert-email configuration state |
| POST | `/alerts/email-config` | ✅ | Verify + save provider API key |
| POST | `/alerts/smtp-config` | ✅ | Verify + save SMTP relay |
| POST | `/alerts/test` | ✅ | Send test alert to Slack + your email |
| GET | `/stores` | ✅ | List stores |
| POST | `/stores` | ✅ | Create store |
| GET | `/cameras` | ✅ | List cameras in your store |
| POST | `/cameras` | ✅ | Create camera in your store |

Static capture images are served at the server root: `GET /uploads/{filename}` (no
`/api/v1` prefix, no auth).

---

## 3. Health

### `GET /health`

Public. Always answers `200`; dependency problems are reported in the body rather than as
a failing status code.

```json
{
  "status": "healthy",
  "services": {
    "api": "healthy",
    "database": "healthy",
    "alert_slack": "not_configured",
    "alert_email": "healthy",
    "alert_email_transport": "smtp"
  }
}
```

| Field | Values |
|-------|--------|
| `status` | `healthy` \| `unhealthy` (only the database flips this) |
| `database` | `healthy` \| `unhealthy: <reason>` |
| `alert_slack` | `healthy` \| `not_configured` |
| `alert_email` | `healthy` \| `not_configured` \| `unhealthy: <reason>` |
| `alert_email_transport` | `smtp` \| `api` \| `none` |

`not_configured` is not an error: it explains why an alert can be recorded without any
message leaving the building.

---

## 4. Authentication

### `POST /auth/signup`

Creates a store and its owner in one transaction. Returns `201` and a token, so the client
does not need a separate login.

**Request**

```json
{
  "store_name": "Downtown Flagship Store",
  "store_location": "12 Main Street",
  "email": "owner@example.com",
  "password": "at-least-8-chars"
}
```

| Field | Rules |
|-------|-------|
| `store_name` | 1–255 chars |
| `store_location` | optional, ≤255 chars |
| `email` | trimmed + lower-cased; must look like `local@domain.tld`; ≤320 chars |
| `password` | 8–72 chars |

**Response `201`**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": { "id": 1, "email": "owner@example.com", "store_id": 1 }
}
```

**Errors** — `409` email already registered; `422` validation.

### `POST /auth/login`

**Request**

```json
{ "email": "owner@example.com", "password": "at-least-8-chars" }
```

**Response `200`** — same `AuthTokenResponse` shape as signup.

**Errors** — `401` `"Incorrect email or password."` for both unknown email and wrong
password (deliberately indistinguishable).

### `GET /auth/me`

**Response `200`**

```json
{ "id": 1, "email": "owner@example.com", "store_id": 1 }
```

**Errors** — `401` when the token is missing, expired, invalid, or the account was deleted.

---

## 5. Captures

### `POST /captures/upload`

`multipart/form-data`. Stores the image, creates the capture, and starts analysis in the
background. Returns immediately with `202`.

| Form field | Type | Required | Default | Notes |
|------------|------|----------|---------|-------|
| `camera_id` | integer | yes | — | Must belong to your store |
| `file` | file | yes | — | `image/*`; extension limited to jpg/jpeg/png/bmp/webp (others stored as `.jpg`) |
| `conf` | float | no | `0.25` | Detection confidence threshold |
| `iou` | float | no | `0.5` | NMS IoU threshold |

**Example**

```bash
curl -X POST http://localhost:8000/api/v1/captures/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "camera_id=3" \
  -F "file=@shelf.jpg"
```

**Response `202`**

```json
{
  "capture_id": 42,
  "status": "processing",
  "image_url": "/uploads/9f2c1e7b8a4d4f3e9c0b1a2d3e4f5a6b.jpg",
  "captured_at": "2026-10-18T09:29:52Z",
  "report_url": "/api/v1/captures/42/report"
}
```

**Errors**

| Status | Cause |
|--------|-------|
| `400` | Non-image content type, or empty file |
| `403` | Camera belongs to another store |
| `404` | Camera does not exist |
| `422` | Missing `camera_id`/`file` |

### `GET /captures/{capture_id}/report`

Poll until `status` is `completed` or `failed`. The frontend polls every 1.5 s with a
120 s timeout.

**While processing (`200`)**

```json
{
  "capture_id": 42,
  "status": "processing",
  "image_url": "/uploads/9f2c1e7b….jpg",
  "captured_at": "2026-10-18T09:29:52Z",
  "fill_percentage": null,
  "low_confidence": false,
  "empty_region_count": null,
  "previous_fill": null,
  "report": null,
  "detections": null,
  "error_message": null
}
```

**Completed (`200`)**

```json
{
  "capture_id": 42,
  "status": "completed",
  "image_url": "/uploads/9f2c1e7b….jpg",
  "captured_at": "2026-10-18T09:29:52Z",
  "fill_percentage": 0.42,
  "low_confidence": false,
  "empty_region_count": 6,
  "previous_fill": 0.61,
  "report": {
    "summary": "Fill decreased 19 points since the last capture; Aisle 3 is below the restock threshold.",
    "urgency": "medium",
    "recommended_action": "Restock the two lowest rows in Aisle 3 before the evening peak."
  },
  "detections": [
    {
      "id": 118,
      "capture_id": 42,
      "class": "empty",
      "bbox": [120.5, 210.0, 430.2, 288.7],
      "confidence": 0.81
    }
  ],
  "error_message": null
}
```

**Failed (`200`)**

```json
{
  "capture_id": 42,
  "status": "failed",
  "image_url": "/uploads/9f2c1e7b….jpg",
  "captured_at": "2026-10-18T09:29:52Z",
  "error_message": "This photo is too blurry to analyse. Please re-upload a sharp photo of the shelf."
}
```

| Field | Meaning |
|-------|---------|
| `empty_region_count` | Number of stored detections (not `empty_regions_json` length) |
| `previous_fill` | Fill fraction of the most recent **confident** report from the same camera with a lower capture id |
| `low_confidence` | `true` when the analyzer could not assess the image |
| `error_message` | Only populated for `failed` |

**Errors** — `404` unknown capture; `403` capture belongs to another store.

---

## 6. Reports

### `GET /reports`

Store-scoped, newest first. All filters combine with AND.

| Query param | Type | Default | Notes |
|-------------|------|---------|-------|
| `aisle` | string | — | Exact match on the camera's aisle |
| `urgency` | string | — | `low` \| `medium` \| `high` |
| `start_date` | ISO-8601 | — | `created_at >= start_date` |
| `end_date` | ISO-8601 | — | `created_at <= end_date` |
| `limit` | int | `50` | 1–200 |
| `offset` | int | `0` | ≥ 0 |

**Example**

```
GET /api/v1/reports?urgency=high&start_date=2026-10-01T00:00:00Z&limit=25&offset=0
```

**Response `200`**

```json
{
  "items": [
    {
      "id": 310,
      "capture_id": 42,
      "fill_percentage": 0.22,
      "low_confidence": false,
      "empty_regions_json": [[120.5, 210.0, 430.2, 288.7]],
      "urgency": "high",
      "llm_summary": "Aisle 3 is critically low after a 39-point drop.",
      "recommended_action": "Dispatch one pallet of the fast-moving SKU immediately.",
      "created_at": "2026-10-18T09:31:07Z",
      "camera_id": 3,
      "camera_name": "Aisle 3 East",
      "aisle": "Aisle 3",
      "store_id": 1,
      "store_name": "Downtown Flagship Store"
    }
  ],
  "total": 1,
  "limit": 25,
  "offset": 0
}
```

`camera_id`, `camera_name`, `aisle`, `store_id`, and `store_name` are resolved through
`report → capture → camera → store`; each is `null` if an upstream row was deleted.

### `GET /reports/export`

Same filters as `GET /reports` (`aisle`, `urgency`, `start_date`, `end_date`), but
**unpaginated** — the complete result set.

**Response `200`** — `text/csv` with:

```
Content-Disposition: attachment; filename=shelf-reports-2026-10-01-to-2026-10-18.csv
```

| Column |
|--------|
| `store_name` |
| `aisle` |
| `camera_name` |
| `fill_percentage` |
| `low_confidence` (`true`/`false`) |
| `urgency` |
| `llm_summary` |
| `recommended_action` |
| `created_at` |

### `GET /reports/{report_id}`

**Response `200`** — a single `ShelfReportResponse` (same object as an `items` entry).

**Errors** — `404` when the report does not exist **or** belongs to another store.

---

## 7. Alerts

### `GET /alerts`

Store-scoped, newest first (`Alert.id DESC`).

| Query param | Type | Default |
|-------------|------|---------|
| `limit` | int | `50` (max 200) |
| `offset` | int | `0` |

**Response `200`**

```json
{
  "items": [
    {
      "id": 57,
      "shelf_report_id": 310,
      "sent_to_slack": true,
      "sent_to_email": true,
      "email_recipients": "owner@example.com, manager@example.com",
      "sent_at": "2026-10-18T09:31:09Z",
      "camera_id": 3,
      "camera_name": "Aisle 3 East",
      "aisle": "Aisle 3",
      "store_id": 1,
      "store_name": "Downtown Flagship Store",
      "urgency": "high",
      "llm_summary": "Aisle 3 is critically low after a 39-point drop.",
      "report_created_at": "2026-10-18T09:31:07Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

| Field | Meaning |
|-------|---------|
| `sent_to_slack` | Slack accepted the message |
| `sent_to_email` | At least one email was delivered |
| `email_recipients` | Comma-joined addresses actually reached |
| `sent_at` | Time Slack accepted the message; `null` if Slack failed/was unconfigured |
| `urgency`, `llm_summary`, `report_created_at` | Copied from the triggering report |

### `GET /alerts/email-config`

Reports the installation-wide email transport.

```json
{
  "configured": true,
  "transport": "smtp",
  "from_address": "ShelfMonitor <alerts@example.com>",
  "last_error": null,
  "key": null,
  "detail": null,
  "email_delivered_to": [],
  "alert_email_to": null,
  "smtp_host": "smtp.gmail.com",
  "smtp_user": "owner@example.com"
}
```

| Field | Meaning |
|-------|---------|
| `configured` | A working transport is configured (`true`/`false`) |
| `transport` | `smtp`, `api`, or `none` |
| `from_address` | Effective `From:` |
| `last_error` | Configuration or last-delivery problem, if any |
| `key` | Masked fingerprint, only on a save response; never the key |
| `alert_email_to` | Global recipient override (`ALERT_EMAIL_TO`), if set |
| `smtp_host`, `smtp_user` | Populated when the active transport is SMTP |

### `POST /alerts/email-config`

Install a provider API key (e.g. Resend). The key is proven with a **real test send**
before anything is written to `backend/.env`; a failure restores the previous settings.

**Request**

```json
{
  "api_key": "re_xxxxxxxxxxxxxxxxxxxxxxxx",
  "from_email": "ShelfMonitor <alerts@yourdomain.com>",
  "alert_email_to": "owner@example.com"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `api_key` | yes | Must look like `re_` + ≥16 chars |
| `from_email` | no | Defaults to `EMAIL_FROM`, SMTP sender, or Resend sandbox |
| `alert_email_to` | no | Comma-separated global override; the first address receives the verification mail |

**Response `200`** — `EmailConfigResponse` with `detail: "key verified and test email delivered"`,
`key` masked, and `email_delivered_to` listing recipients.

**Errors** — `400` with the provider's reason when the key is malformed or delivery fails.

### `POST /alerts/smtp-config`

Install an SMTP relay (recommended: Gmail App Password). One relay serves every account and
can deliver to **any** login address.

**Request**

```json
{
  "host": "smtp.gmail.com",
  "port": 587,
  "user": "alerts@yourdomain.com",
  "password": "your-app-password",
  "smtp_from": "ShelfMonitor <alerts@yourdomain.com>",
  "use_tls": true,
  "use_ssl": false
}
```

| Field | Required | Default |
|-------|----------|---------|
| `host` | yes | — |
| `port` | no | `587` |
| `user` | yes | — |
| `password` | yes | — |
| `smtp_from` | no | falls back to `user` |
| `use_tls` | no | `true` (STARTTLS, port 587) |
| `use_ssl` | no | `false` (set `true` for implicit TLS on 465) |

**Response `200`** — `EmailConfigResponse` with `detail: "SMTP relay verified and test email delivered"`.

**Errors** — `400` when the relay rejects the credentials or the test send fails. The
previous working configuration is preserved.

> Installing SMTP retires any saved API key, and installing an API key clears the SMTP
> settings — only one transport is active at a time.

### `POST /alerts/test`

Sends a diagnostic alert through the same code path as a real alert:

- Slack, if `SLACK_WEBHOOK_URL` is set;
- email to **the caller's own login address**, if a transport is configured.

No request body.

**Response `200`**

```json
{
  "status": "sent",
  "detail": "Test alert delivered via Slack and email to owner@example.com.",
  "slack": "sent",
  "email": "sent",
  "email_recipients": ["owner@example.com"]
}
```

`slack` / `email` are `sent`, `failed`, or `not_configured`.

**Errors**

| Status | Cause |
|--------|-------|
| `503` | Neither Slack nor email is configured |
| `502` | Configured channel(s) all failed to deliver |

---

## 8. Stores

### `GET /stores`

Authenticated. Returns **every** store in the directory (reference data), ordered by id.

```json
[
  { "id": 1, "name": "Downtown Flagship Store", "location": "12 Main Street" },
  { "id": 2, "name": "Westside Supermarket", "location": null }
]
```

### `POST /stores`

**Request**

```json
{ "name": "Westside Supermarket", "location": "5 West Road" }
```

**Response `201`**

```json
{ "id": 2, "name": "Westside Supermarket", "location": "5 West Road" }
```

**Errors** — `401`, `422`.

---

## 9. Cameras

### `GET /cameras`

Authenticated. Returns only the cameras belonging to the caller's store, ordered by id.

```json
[
  { "id": 3, "store_id": 1, "aisle": "Aisle 3", "name": "Aisle 3 East" },
  { "id": 4, "store_id": 1, "aisle": "Aisle 4", "name": "Aisle 4 West" }
]
```

### `POST /cameras`

Creates a camera in the caller's store. `store_id` in the body is accepted for backwards
compatibility but **ignored**.

**Request**

```json
{ "aisle": "Aisle 4", "name": "Aisle 4 West" }
```

**Response `201`**

```json
{ "id": 4, "store_id": 1, "aisle": "Aisle 4", "name": "Aisle 4 West" }
```

**Errors** — `404` if the caller's store is missing; `422` when `aisle`/`name` are absent.

---

## 10. Response schemas

### `AuthTokenResponse`

| Field | Type | Notes |
|-------|------|-------|
| `access_token` | string | Bearer token |
| `token_type` | string | Always `"bearer"` |
| `expires_in` | int | Seconds |
| `user` | `UserResponse` | |

### `UserResponse`

| Field | Type |
|-------|------|
| `id` | int |
| `email` | string |
| `store_id` | int |

### `ShelfReportResponse`

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | |
| `capture_id` | int | |
| `fill_percentage` | float | Fraction `0..1` |
| `low_confidence` | bool | Not a measurement when `true` |
| `empty_regions_json` | array \| null | Bounding boxes |
| `urgency` | string | `low` \| `medium` \| `high` |
| `llm_summary` | string \| null | |
| `recommended_action` | string \| null | |
| `created_at` | string | UTC `…Z` |
| `camera_id` | int \| null | |
| `camera_name` | string \| null | |
| `aisle` | string \| null | |
| `store_id` | int \| null | |
| `store_name` | string \| null | |

### `AlertResponse`

| Field | Type |
|-------|------|
| `id` | int |
| `shelf_report_id` | int |
| `sent_to_slack` | bool |
| `sent_to_email` | bool |
| `email_recipients` | string \| null |
| `sent_at` | string \| null |
| `camera_id`, `camera_name`, `aisle`, `store_id`, `store_name` | nullable |
| `urgency` | `low` \| `medium` \| `high` \| null |
| `llm_summary` | string \| null |
| `report_created_at` | string \| null |

### `DetectionResponse`

| Field | Type | Notes |
|-------|------|-------|
| `id` | int | |
| `capture_id` | int | |
| `class` | string | `"empty"` for the shipped model |
| `bbox` | array | `[xmin, ymin, xmax, ymax]` |
| `confidence` | float | `0..1` |

---

## 11. Quick start with curl

```bash
BASE=http://localhost:8000/api/v1

# 1. Create an account (returns a token)
TOKEN=$(curl -s -X POST $BASE/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"store_name":"Demo Store","email":"owner@example.com","password":"password123"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Add a camera
curl -s -X POST $BASE/cameras \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"aisle":"Aisle 1","name":"Front shelf"}'

# 3. Upload a capture (202 + capture_id)
curl -s -X POST $BASE/captures/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "camera_id=1" -F "file=@shelf.jpg"

# 4. Poll the report
curl -s $BASE/captures/1/report -H "Authorization: Bearer $TOKEN"

# 5. List high-urgency reports and alerts
curl -s "$BASE/reports?urgency=high" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/alerts?limit=10"     -H "Authorization: Bearer $TOKEN"

# 6. Confirm alerting works
curl -s -X POST $BASE/alerts/test -H "Authorization: Bearer $TOKEN"
```

---

## 12. Static images

`GET /uploads/{filename}` serves files from `UPLOAD_DIR` (default `backend/uploads`).
URLs are server-root-relative (`/uploads/<uuid>.jpg`), which is why the frontend's
`resolveImageUrl()` rebuilds them against the API origin rather than appending them to
`/api/v1`.
