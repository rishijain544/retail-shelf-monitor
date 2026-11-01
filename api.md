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

Tokens are HS256 JWTs with `sub = user id` and a `purpose` claim; an `access` token expires
after `ACCESS_TOKEN_EXPIRE_MINUTES` (default 1440 minutes = 24 h). It is issued by
`POST /auth/mfa/verify` (or by `POST /auth/mfa/setup/confirm` when an account finishes
enrolment), never by signup.

Sign-in is two-step for ordinary accounts: a correct password yields a short-lived token
that opens exactly one endpoint.

| `purpose` | Lifetime | Accepted by |
|-----------|----------|-------------|
| `access` | 24 h | every private endpoint |
| `mfa_challenge` | 5 min | `POST /auth/mfa/verify` only |
| `mfa_setup` | 15 min | `POST /auth/mfa/setup/start`, `POST /auth/mfa/setup/confirm` only |

A token with any other purpose (or with no `purpose` claim at all, such as one minted
before this scheme existed) is rejected on every endpoint with `401` and
`WWW-Authenticate: Bearer` — clients must sign in again.

Every token also carries `iat`. A token with no usable `iat` is rejected, and so is any
token issued before the account's `users.password_changed_at`; `POST /auth/reset-password`
moves that timestamp forward, which signs the account out everywhere — access tokens and
in-flight `mfa_challenge`/`mfa_setup` tokens alike.

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
| `429` | Login/MFA lockout, or a verification-code resend inside the 60 s cooldown |
| `500` | Unhandled server error (`{"detail": "Internal server error"}`) |
| `502` | Alert channels configured but every one failed |
| `503` | No alert channel configured |

Auth failures add a machine-readable `code` next to the human `detail`:

```json
{ "detail": "Your email address is not verified yet. …", "code": "email_unverified" }
```

| `code` | Status | Meaning |
|--------|--------|---------|
| `email_unverified` | 403 | Password correct, address not confirmed |
| `account_locked` | 429 | Too many failed passwords |
| `mfa_locked` | 429 | Too many failed second factors |
| `mfa_challenge_invalid` | 401 | Challenge token expired/forged |
| `mfa_code_invalid` | 401 | Wrong TOTP/recovery code |
| `resend_cooldown` | 429 | A verification email was just sent |
| `invalid_password` | 401 | Recovery-code regeneration needs the password |
| `invalid_totp_code` | 401 | Recovery-code regeneration needs a current TOTP |

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
| POST | `/auth/signup` | — | Create store + owner, email a verification code (no token) |
| POST | `/auth/verify-email` | — | Confirm the address, return an `mfa_setup` token |
| POST | `/auth/resend-verification` | — | New verification code (60 s cooldown) |
| POST | `/auth/forgot-password` | — | Email a reset link (identical 202 for any address) |
| POST | `/auth/reset-password` | — | Set a new password from a reset link (no session) |
| POST | `/auth/login` | — | Password step: session, MFA challenge, or enrolment |
| POST | `/auth/mfa/verify` | challenge | TOTP or recovery code → `access` token |
| POST | `/auth/mfa/setup/start` | setup | Issue an authenticator secret |
| POST | `/auth/mfa/setup/confirm` | setup | Enable MFA, return recovery codes + `access` token |
| POST | `/auth/mfa/recovery-codes/regenerate` | ✅ | Replace recovery codes (password + TOTP) |
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
    "schema": "head",
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
| `schema` | `head` \| `outdated: <rev> (head <rev>)` \| `not_initialised: <reason>` |
| `alert_slack` | `healthy` \| `not_configured` |
| `alert_email` | `healthy` \| `not_configured` \| `unhealthy: <reason>` |
| `alert_email_transport` | `smtp` \| `api` \| `none` |

`not_configured` is not an error: it explains why an alert can be recorded without any
message leaving the building.

`database` reports reachability only — `SELECT 1` succeeds on a database with no tables. A
schema that is not at head means every `POST /auth/signup` and `POST /auth/login` answers
`500`, which is what `schema` exists to make visible.

---

## 4. Authentication

### `POST /auth/signup`

Creates a store and its owner in one transaction, sets `email_verified = false`, mails a
six-digit code to the address, and returns **`202` with no token**. A new account cannot
sign in until that code is confirmed, so a typo'd or someone else's address never becomes
a working login.

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
| `password` | 8–72 chars, and at most 72 bytes in UTF-8 (bcrypt's limit) |

**Response `202`**

```json
{
  "status": "verification_required",
  "email": "owner@example.com",
  "expires_in": 600
}
```

**Errors** — `409` email already registered; `422` validation.

### `POST /auth/verify-email`

Consumes the emailed code. At most 5 wrong guesses per code, after which it is burned; a
code expires after `VERIFICATION_CODE_TTL_MINUTES` (default 10). A wrong code and an
unknown address return the same `400`.

```json
{ "email": "owner@example.com", "code": "483920" }
```

**Response `200`**

```json
{
  "status": "email_verified",
  "setup_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
  "expires_in": 900
}
```

`setup_token` is a `mfa_setup` token: it opens only the two `/auth/mfa/setup/*` routes.

### `POST /auth/resend-verification`

```json
{ "email": "owner@example.com" }
```

**Response `202`** — `{ "status": "verification_sent" }` (also for an unknown address, so
the endpoint is not an account oracle), or `{ "status": "already_verified" }`. A second
request inside 60 seconds returns `429` with `code: "resend_cooldown"`.

### `POST /auth/forgot-password`

```json
{ "email": "owner@example.com" }
```

**Response `202`** — always, for every address:

```json
{ "status": "reset_requested", "message": "If that address has an account, a reset link is on its way." }
```

The lookup and the mail are a background task, so an unknown address is not faster than a
known one. A link is only created and mailed when the account exists, is not `mfa_exempt`,
and its address does not end in `.test` (so the demo login can never be reset). One email
per `PASSWORD_RESET_COOLDOWN_SECONDS` (default 60) per account; a per-IP budget of 10/hour
is also applied. Both limits still answer the same `202` and are only logged.

The link is `{FRONTEND_BASE_URL}/reset-password?token=…` and expires after
`PASSWORD_RESET_TTL_MINUTES` (default 30). Only the token's SHA-256 digest is stored, and
a new request invalidates every earlier unused link for that account.

### `POST /auth/reset-password`

```json
{ "token": "<token from the emailed link>", "new_password": "at-least-8-chars" }
```

**Response `200`** — a status only; there is no token of any kind in it:

```json
{ "status": "password_reset", "message": "Your password has been changed. Sign in with your new password." }
```

`new_password` uses the same rule as signup (`8`–`72` characters **and** at most 72 bytes
in UTF-8 — 40 characters of 3-byte text is 120 bytes and is rejected), so a `422` means the
password itself is unacceptable. On success the password is re-hashed, `email_verified`
becomes `true`, `password_changed_at` moves forward (invalidating every existing JWT), a
failed-login lockout is cleared, and the user is emailed a "password changed" notice. MFA
state, its lockout and the recovery codes are untouched — signing in still requires the
authenticator code.

**Errors** — `400` `reset_token_invalid` for an unknown, expired, already-used or
superseded link (one message for all four); `429` `password_reset_rate_limited` after 20
attempts from one IP in an hour.

### `POST /auth/login`

**Request**

```json
{ "email": "owner@example.com", "password": "at-least-8-chars" }
```

**Response `200`** — one of three shapes:

```json
{ "access_token": "…", "token_type": "bearer", "expires_in": 86400, "user": { "id": 1, "email": "owner@example.com", "store_id": 1, "email_verified": true, "mfa_enabled": false } }
```

```json
{ "status": "mfa_required", "challenge_token": "…", "expires_in": 300 }
```

```json
{ "status": "mfa_setup_required", "setup_token": "…", "expires_in": 900 }
```

The first is returned only for an `mfa_exempt` account (the shared demo login). Everything
else must continue at `/auth/mfa/verify` or `/auth/mfa/setup/*`.

**Errors** — `401` `"Invalid email or password."` for both unknown email and wrong password
(deliberately indistinguishable); `403` `email_unverified`; `429` `account_locked` after
5 failed passwords, for `LOCKOUT_MINUTES` (default 15).

### `POST /auth/mfa/verify`

```json
{ "challenge_token": "…", "code": "483920" }
```

`code` is either the current 6-digit TOTP code or one of the printed recovery codes
(`abcd-ef12`, case and separators ignored).

**Response `200`** — the `AuthTokenResponse` above, with a real access token.

Replay protection: a TOTP code is accepted once — its time step must be newer than the
last accepted one. A recovery code is marked used. Five wrong codes lock the account for
`LOCKOUT_MINUTES` (`429`, `code: "mfa_locked"`).

### `POST /auth/mfa/setup/start`

Requires a `mfa_setup` token. Generates a new secret, stores it encrypted (not yet
enabled), and returns it once. Calling again replaces the pending secret.

```json
{
  "secret": "JBSWY3DPEHPK3PXP",
  "otpauth_uri": "otpauth://totp/ShelfMonitor:owner%40example.com?secret=…&issuer=ShelfMonitor",
  "issuer": "ShelfMonitor",
  "account": "owner@example.com"
}
```

### `POST /auth/mfa/setup/confirm`

Requires a `mfa_setup` token.

```json
{ "code": "483920" }
```

**Response `200`** — an `AuthTokenResponse` plus the recovery codes, which are shown
**exactly once**.

```json
{
  "access_token": "…",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": { "id": 1, "email": "owner@example.com", "store_id": 1, "email_verified": true, "mfa_enabled": true },
  "recovery_codes": ["68ed-930c", "…10 total"]
}
```

### `POST /auth/mfa/recovery-codes/regenerate`

Requires an `access` token **and** both factors, so a stolen session cannot mint itself a
permanent bypass.

```json
{ "password": "at-least-8-chars", "code": "483920" }
```

**Response `200`** — `{ "status": "recovery_codes_regenerated", "recovery_codes": [ … ] }`.
All previous codes stop working.

### `GET /auth/me`

**Response `200`**

```json
{
  "id": 1,
  "email": "owner@example.com",
  "store_id": 1,
  "email_verified": true,
  "mfa_enabled": true
}
```

**Errors** — `401` when the token is missing, expired, invalid, of the wrong purpose, or the
account was deleted.

> There is deliberately **no endpoint that disables MFA**: a stolen password or session
> must not be able to remove a second factor. The only recovery path for a lost device and
> lost codes is the operator script `backend/scripts/reset_mfa.py <email>`.

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
before anything is stored; a failure restores the previous settings.

On success the verified transport is saved to the singleton `email_settings` row (not
`backend/.env`), so it survives a redeploy on a read-only container filesystem and is
reapplied at startup.

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

**Response `200`** — `EmailConfigResponse` with `detail: "key verified, saved and test email delivered"`,
`key` masked, and `email_delivered_to` listing recipients.

**Errors** — `400` with the provider's reason when the key is malformed or delivery fails;
`500` when the key is verified but the database write fails (the message says so, and the
credential remains active only for the current process).

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

**Response `200`** — `EmailConfigResponse` with
`detail: "SMTP relay verified, saved and test email delivered"`.

**Errors** — `400` when the relay rejects the credentials or the test send fails (the
previous working configuration is preserved); `500` when verification succeeded but the
database write failed.

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
| `email_verified` | bool |
| `mfa_enabled` | bool |

The MFA secret, its ciphertext, the replay step and the lockout counters are never
returned.

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

# 1. Sign in. An ordinary account answers {"status": "mfa_required", "challenge_token": …}
#    (then POST /auth/mfa/verify) or {"status": "mfa_setup_required", …} (enrol first).
#    The mfa_exempt demo account returns a token directly:
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@shelfmonitor.test","password":"demo-2026-shelf"}' \
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
