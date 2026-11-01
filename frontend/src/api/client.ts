/**
 * frontend/src/api/client.ts
 *
 * Typed client for the FastAPI backend (backend/app/api/v1/).
 *
 * Every function here maps to a route that actually exists on the backend today.
 * Shape details worth knowing before you use it:
 *
 *   * GET /reports and GET /alerts return a `Page<T>` envelope
 *     ({ items, total, limit, offset }) — NOT a bare array. GET /stores and
 *     GET /cameras do return bare arrays.
 *   * POST /captures/upload answers 202 immediately with a receipt
 *     (capture_id + report_url + status "processing"); the analysis runs in a
 *     background task. Use pollCaptureReport() to fetch the result.
 *   * A capture report's status is "processing" | "completed" | "failed".
 *     ("ready" is the internal captures.status DB value on success — the API
 *     maps it to "completed", so the wire never carries "ready".)
 */

/** Base URL including the /api/v1 prefix, e.g. "http://localhost:8000/api/v1". */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

/* ------------------------------------------------------------------ *
 * Shared types
 * ------------------------------------------------------------------ */

/** Envelope returned by the paginated list endpoints (backend Page[T]). */
export interface Page<T> {
  items: T[];
  /** Total rows matching the query, ignoring limit/offset. */
  total: number;
  /** Max rows requested for this page. */
  limit: number;
  /** Rows skipped before this page. */
  offset: number;
}

/** The ordinary urgency levels of a shelf report. */
export type Urgency = 'low' | 'medium' | 'high';

/** Lifecycle of the background analysis pipeline, as reported by the API. */
export type CaptureStatus = 'processing' | 'completed' | 'failed';

export interface PageParams {
  /** 1–200, backend default 50. */
  limit?: number;
  /** >= 0, backend default 0. */
  offset?: number;
}

/* ------------------------------------------------------------------ *
 * Resource types
 * ------------------------------------------------------------------ */

export interface ServiceStatus {
  api: string;
  /** "healthy", or "unhealthy: <reason>". */
  database: string;
  /**
   * Alembic schema state: "head" when the migration chain is applied, otherwise
   * "outdated: <rev> (head <rev>)" or "not_initialised: <reason>". A reachable
   * database with an unapplied schema still fails every signup and login.
   */
  schema?: string;
  /** "healthy" when a Slack webhook is set, else "not_configured". */
  alert_slack?: string;
  /** "healthy" when an email transport (API key or SMTP) is set, else "not_configured". */
  alert_email?: string;
  /** Which email transport would be used: "api", "smtp" or "none". */
  alert_email_transport?: string;
}

export interface HealthData {
  /** "healthy" | "unhealthy" */
  status: string;
  services: ServiceStatus;
}

export interface Store {
  id: number;
  name: string;
  location: string | null;
}

export interface Camera {
  id: number;
  store_id: number;
  aisle: string;
  name: string;
}

export interface Detection {
  id: number;
  capture_id: number;
  /**
   * Detected class name — "empty" for the empty-space model. Serialized under
   * the JSON key "class" (the backend field is `class_` with alias "class").
   */
  class: string;
  /** Bounding box coordinates as stored by the pipeline. */
  bbox: number[];
  confidence: number;
}

export interface ShelfReport {
  id: number;
  capture_id: number;
  /**
   * Fraction in 0..1, not 0..100 — multiply by 100 to display a percentage.
   *
   * On a low_confidence report this number is the detector's vacuous 1.0 ("nothing
   * found"), NOT a measurement — render low_confidence reports as "unable to assess"
   * instead of presenting them as a genuine 100% full shelf.
   */
  fill_percentage: number;
  /**
   * True when the analysis produced zero confident detections. The fill/urgency pair
   * on such a report is not a real assessment: the UI must show a distinct
   * "unable to assess" treatment, and such reports are excluded from alerting and
   * from trend baselines server-side.
   */
  low_confidence: boolean;
  empty_regions_json: unknown[] | null;
  urgency: Urgency;
  llm_summary: string | null;
  recommended_action: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /**
   * Capture context, resolved server-side through capture -> camera -> store.
   * The shelf_reports row only stores capture_id, so the API joins these in for
   * listings. Null when the originating camera or store row no longer exists.
   */
  camera_id: number | null;
  camera_name: string | null;
  aisle: string | null;
  store_id: number | null;
  store_name: string | null;
}

/** Structured LLM output embedded in a completed capture report. */
export interface ShelfReportOutput {
  summary: string;
  urgency: Urgency;
  recommended_action: string;
}

export interface Alert {
  id: number;
  shelf_report_id: number;
  sent_to_slack: boolean;
  /**
   * Whether the same alert was also emailed to the store's login address(es).
   * False when SMTP is unconfigured, the store has no users, or delivery failed.
   */
  sent_to_email: boolean;
  /** Comma-joined addresses the email reached, or null when none did. */
  email_recipients: string | null;
  /** ISO-8601 timestamp, null when the Slack delivery never succeeded. */
  sent_at: string | null;
  /**
   * Context resolved server-side through shelf_report -> capture -> camera ->
   * store. Null when an upstream row no longer exists.
   */
  camera_id: number | null;
  camera_name: string | null;
  aisle: string | null;
  store_id: number | null;
  store_name: string | null;
  /** Copied from the shelf report this alert fired for. */
  urgency: Urgency | null;
  llm_summary: string | null;
  report_created_at: string | null;
}

/**
 * The authenticated user's own record, as returned by GET /auth/me and by the
 * endpoints that finish a sign-in.
 *
 * The two flags are optional because they were added with MFA: an older cached
 * payload (or a test double) may not carry them. Treat `undefined` as "unknown",
 * never as false, when deciding what to tell the user.
 */
export interface User {
  id: number;
  email: string;
  store_id: number;
  email_verified?: boolean;
  mfa_enabled?: boolean;
}

/**
 * A full session: the only token this app ever persists.
 *
 * Returned by the demo login (and any `mfa_exempt` account), by POST
 * /auth/mfa/verify after the second factor, and by POST /auth/mfa/setup/confirm
 * once enrolment is complete.
 */
export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

/**
 * 202 from POST /auth/signup. Deliberately has no token: the address is not a
 * usable login until the emailed code is confirmed.
 */
export interface SignupPendingResponse {
  status: 'verification_required';
  email: string;
  expires_in: number;
}

/** 200 from POST /auth/verify-email — a token that only opens the enrolment routes. */
export interface VerifyEmailResponse {
  status: 'email_verified';
  setup_token: string;
  expires_in: number;
}

/** Password accepted, but the account has an authenticator enrolled. */
export interface MfaChallengeResponse {
  status: 'mfa_required';
  challenge_token: string;
  expires_in: number;
}

/** Password accepted, but no authenticator is enrolled yet (mandatory). */
export interface MfaSetupRequiredResponse {
  status: 'mfa_setup_required';
  setup_token: string;
  expires_in: number;
}

/**
 * POST /auth/login answers with one of three shapes. Only the first is a session;
 * the other two carry a short-lived token that belongs in memory, never in
 * localStorage (it is not a login and must not survive a reload).
 */
export type LoginResponse =
  | AuthTokenResponse
  | MfaChallengeResponse
  | MfaSetupRequiredResponse;

/** True when the password step succeeded and a second factor is required. */
export function isMfaChallenge(body: LoginResponse): body is MfaChallengeResponse {
  return (body as MfaChallengeResponse).status === 'mfa_required';
}

/** True when the password step succeeded but no authenticator is enrolled yet. */
export function isMfaSetupRequired(
  body: LoginResponse,
): body is MfaSetupRequiredResponse {
  return (body as MfaSetupRequiredResponse).status === 'mfa_setup_required';
}

/** 200 from POST /auth/mfa/setup/start — shown once, then never retrievable. */
export interface MfaSetupStartResponse {
  secret: string;
  otpauth_uri: string;
  issuer: string;
  account: string;
}

/** Enrolment finished: a session plus the one-time recovery codes. */
export interface MfaSetupConfirmResponse extends AuthTokenResponse {
  recovery_codes: string[];
}

/** Generic `{ status, message }` receipt used by the resend endpoint. */
export interface AuthStatusResponse {
  status: string;
  message?: string | null;
}

/** 202 receipt from POST /captures/upload — this is NOT the analysis result. */
export interface CaptureUploadReceipt {
  capture_id: number;
  status: 'processing';
  /** Server-root-relative, e.g. "/uploads/<uuid>.jpg" — see resolveImageUrl(). */
  image_url: string;
  captured_at: string;
  /** Absolute API path, e.g. "/api/v1/captures/12/report" (already prefixed). */
  report_url: string;
}

/** GET /captures/{id}/report. Result fields are null until status is "completed". */
export interface CaptureReport {
  capture_id: number;
  status: CaptureStatus;
  image_url: string | null;
  captured_at: string | null;
  fill_percentage: number | null;
  /** True when the analysis produced zero confident detections (see ShelfReport). */
  low_confidence: boolean;
  empty_region_count: number | null;
  /** Fill fraction of the previous capture from the same camera, if any. */
  previous_fill: number | null;
  report: ShelfReportOutput | null;
  detections: Detection[] | null;
  /** Populated only when status is "failed". */
  error_message: string | null;
}

export interface ReportFilters extends PageParams {
  /**
   * Deprecated: the current backend derives store scope from the authenticated
   * user's store_id, so this param is ignored now. Kept here only for backwards
   * compatibility with older frontend call-sites that still pass it. Remove it
   * when those call-sites are migrated to the scoped endpoints.
   */
  store_id?: number;
  aisle?: string;
  urgency?: Urgency;
  /** ISO-8601 string or Date — reports created at or after this instant. */
  start_date?: string | Date;
  /** ISO-8601 string or Date — reports created at or before this instant. */
  end_date?: string | Date;
}

export interface UploadCaptureParams {
  camera_id: number;
  file: File;
  /** Detection confidence threshold, backend default 0.4. */
  conf?: number;
  /** NMS IoU threshold, backend default 0.5. */
  iou?: number;
}

export interface TestAlertResult {
  status: string;
  detail: string;
  /** Per-channel outcome of the diagnostic send. */
  slack?: 'sent' | 'failed' | 'not_configured';
  email?: 'sent' | 'failed' | 'not_configured';
  /** Addresses the test email actually reached (empty when it was not sent). */
  email_recipients?: string[];
}

/* ------------------------------------------------------------------ *
 * Transport
 * ------------------------------------------------------------------ */

/** A non-2xx response, carrying FastAPI's `detail` message when there is one. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;
  /**
   * Machine-readable reason for auth failures, e.g. `"email_unverified"`,
   * `"account_locked"`, `"mfa_locked"`, `"mfa_challenge_invalid"`. Branch on this
   * rather than on the wording of `detail`, which is meant for humans.
   */
  readonly code: string | null;

  constructor(status: number, detail: string | null, url: string, code: string | null = null) {
    super(detail ?? `Request to ${url} failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

type QueryValue = string | number | boolean | Date | undefined | null;

function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.append(key, value instanceof Date ? value.toISOString() : String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

/**
 * Pull FastAPI's `{ "detail": …, "code": … }` out of an error body.
 *
 * `detail` is always shown to a person; `code` is what the auth screens branch on
 * (lockout vs. wrong code vs. expired challenge), so both are kept.
 */
async function readErrorBody(
  response: Response,
): Promise<{ detail: string | null; code: string | null }> {
  try {
    const body = (await response.json()) as { detail?: unknown; code?: unknown };
    const detail = body?.detail;
    return {
      detail:
        typeof detail === 'string' ? detail : detail !== undefined ? JSON.stringify(detail) : null,
      code: typeof body?.code === 'string' ? body.code : null,
    };
  } catch {
    return { detail: null, code: null };
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const mergedInit: RequestInit = {
    ...init,
    // Caller headers win: the MFA flow passes its short-lived setup/challenge
    // token explicitly, and it must not be replaced by a stale persisted one.
    headers: {
      ...(activeAuthHeaders() ?? {}),
      ...(init?.headers ?? {}),
    },
  };
  const response = await fetch(url, mergedInit);
  if (!response.ok) {
    const { detail, code } = await readErrorBody(response);
    throw new ApiError(response.status, detail, url, code);
  }
  return (await response.json()) as T;
}

function activeAuthHeaders(): Record<string, string> | undefined {
  const token = currentToken();
  if (!token) return undefined;
  return { Authorization: `Bearer ${token}` };
}

/**
 * Turn a server-root-relative `image_url` ("/uploads/x.jpg") into an absolute
 * URL. Uploaded images are served at the server root, outside the /api/v1
 * prefix, so they cannot just be appended to API_BASE_URL.
 */
export function resolveImageUrl(imageUrl: string): string {
  if (/^https?:\/\//i.test(imageUrl)) return imageUrl;
  const origin = new URL(API_BASE_URL, window.location.origin).origin;
  return `${origin}${imageUrl.startsWith('/') ? '' : '/'}${imageUrl}`;
}

/* ------------------------------------------------------------------ *
 * health
 * ------------------------------------------------------------------ */

/**
 * Current auth token, if any.
 *
 * Exported so the transport layer (request()) can read it without importing
 * React hook state. The authoritative source is still the persisted localStorage
 * entry managed by useAuth.
 *
 * Same security note as in useAuth: localStorage is acceptable for this project's
 * scope, but httpOnly same-site cookies would be the more secure production choice
 * (not exposed to XSS, no manual CSRF plumbing needed). Out of scope here.
 */
export function currentToken(): string | undefined {
  try {
    const raw = localStorage.getItem('sm:auth:state');
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as { kind: 'authenticated'; token: string } | { kind: 'unauthenticated' };
    return parsed.kind === 'authenticated' && typeof parsed.token === 'string' ? parsed.token : undefined;
  } catch {
    return undefined;
  }
}

/** GET /health — API and database liveness. */
export function getHealth(signal?: AbortSignal): Promise<HealthData> {
  return request<HealthData>('/health', { signal });
}

/* ------------------------------------------------------------------ *
 * captures
 * ------------------------------------------------------------------ */

/**
 * POST /captures/upload — multipart upload of a shelf image.
 *
 * Returns 202 with a receipt as soon as the file is stored; analysis runs in a
 * background task. The receipt's `status` is always "processing" — poll
 * pollCaptureReport(receipt.capture_id) for the actual report.
 */
export function uploadCapture(
  { camera_id, file, conf, iou }: UploadCaptureParams,
  signal?: AbortSignal,
): Promise<CaptureUploadReceipt> {
  const form = new FormData();
  form.append('camera_id', String(camera_id));
  form.append('file', file);
  if (conf !== undefined) form.append('conf', String(conf));
  if (iou !== undefined) form.append('iou', String(iou));
  // No Content-Type header: the browser sets the multipart boundary itself.
  return request<CaptureUploadReceipt>('/captures/upload', {
    method: 'POST',
    body: form,
    signal,
  });
}

/**
 * GET /captures/{id}/report — one poll of the background pipeline's result.
 *
 * `status` is "processing" while the pipeline runs, "completed" once the report
 * fields are populated, or "failed" with `error_message` set. Throws ApiError
 * (404) if the capture id does not exist.
 */
export function pollCaptureReport(
  captureId: number,
  signal?: AbortSignal,
): Promise<CaptureReport> {
  return request<CaptureReport>(`/captures/${captureId}/report`, { signal });
}

/**
 * Poll pollCaptureReport() until the capture reaches a terminal status
 * ("completed" or "failed") and return that report.
 *
 * Rejects if `timeoutMs` elapses first or the signal aborts. The final report is
 * returned as-is for "failed" too — check `status` rather than assuming success.
 */
export async function waitForCaptureReport(
  captureId: number,
  options: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<CaptureReport> {
  const { intervalMs = 2000, timeoutMs = 120000, signal } = options;
  const deadline = Date.now() + timeoutMs;

  for (;;) {
    const report = await pollCaptureReport(captureId, signal);
    if (report.status !== 'processing') return report;
    if (Date.now() + intervalMs > deadline) {
      throw new Error(`Capture ${captureId} still processing after ${timeoutMs}ms.`);
    }
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', onAbort);
        resolve();
      }, intervalMs);
      const onAbort = () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      };
      if (signal?.aborted) return onAbort();
      signal?.addEventListener('abort', onAbort, { once: true });
    });
  }
}

/* ------------------------------------------------------------------ *
 * reports
 * ------------------------------------------------------------------ */

/**
 * GET /reports — a page of shelf reports, newest first.
 *
 * All filters are optional and combine with AND. `total` in the envelope counts
 * every row matching the filters, independent of limit/offset.
 */
export function listReports(
  filters: ReportFilters = {},
  signal?: AbortSignal,
): Promise<Page<ShelfReport>> {
  const { store_id, aisle, urgency, start_date, end_date, limit, offset } = filters;
  const query = buildQuery({ store_id, aisle, urgency, start_date, end_date, limit, offset });
  return request<Page<ShelfReport>>(`/reports${query}`, { signal });
}

/** GET /reports/{id} — a single shelf report. Throws ApiError (404) if unknown. */
export function getReport(reportId: number, signal?: AbortSignal): Promise<ShelfReport> {
  return request<ShelfReport>(`/reports/${reportId}`, { signal });
}

/**
 * GET /reports/export — CSV export of every shelf report matching the given
 * filters (unpaginated, complete result set).
 *
 * Returns a Response with Content-Type text/csv and a Content-Disposition
 * header; the caller is responsible for triggering the browser download.
 */
export function exportReportsCsv(
  filters: ReportFilters = {},
  signal?: AbortSignal,
): Promise<Response> {
  const { store_id, aisle, urgency, start_date, end_date } = filters;
  const query = buildQuery({ store_id, aisle, urgency, start_date, end_date });
  return fetch(`${API_BASE_URL}/reports/export${query}`, {
    method: 'GET',
    signal,
  });
}

/* ------------------------------------------------------------------ *
 * alerts
 * ------------------------------------------------------------------ */

/** GET /alerts — a page of alerts, newest first. One alert per high-urgency report. */
export function listAlerts(
  params: PageParams = {},
  signal?: AbortSignal,
): Promise<Page<Alert>> {
  const query = buildQuery({ limit: params.limit, offset: params.offset });
  return request<Page<Alert>>(`/alerts${query}`, { signal });
}

/**
 * POST /alerts/test — send a diagnostic message through every configured alert
 * channel: the Slack webhook and email to the caller's own login address.
 *
 * Throws ApiError 503 when neither channel is configured, or 502 when every
 * configured channel failed to deliver.
 */
export function sendTestAlert(signal?: AbortSignal): Promise<TestAlertResult> {
  return request<TestAlertResult>('/alerts/test', { method: 'POST', signal });
}

/**
 * State of the alert-email transport: whether it can send, over which transport, and
 * why the last attempt failed (`last_error`), so the UI never claims "configured"
 * while a bad key is rejecting every message.
 */
export interface EmailConfig {
  configured: boolean;
  /** "api" | "smtp" | "none" */
  transport: string;
  /** The From: address mail will be sent as. */
  from_address: string;
  last_error: string | null;
  /** Masked fingerprint of the saved key (never the key itself). */
  key: string | null;
  detail: string | null;
  email_delivered_to: string[];
  /** Comma-separated global recipient override (ALERT_EMAIL_TO), or null. */
  alert_email_to: string | null;
  /** SMTP relay host, when the active transport is SMTP. */
  smtp_host: string | null;
  /** SMTP login, when the active transport is SMTP. */
  smtp_user: string | null;
}

/** GET /alerts/email-config — current alert-email configuration. */
export function getEmailConfig(signal?: AbortSignal): Promise<EmailConfig> {
  return request<EmailConfig>('/alerts/email-config', { signal });
}

/**
 * POST /alerts/email-config — verify a provider API key, save it to backend/.env, apply
 * it immediately and send a test message to the signed-in user.
 *
 * Throws ApiError 400 when the provider rejects the key.
 */
export function saveEmailConfig(
  data: { api_key: string; from_email?: string | null; alert_email_to?: string | null },
  signal?: AbortSignal,
): Promise<EmailConfig> {
  return request<EmailConfig>('/alerts/email-config', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/** SMTP relay credentials for per-account alert email (a Gmail App Password). */
export interface SmtpConfig {
  host: string;
  port: number;
  user: string;
  password: string;
  smtp_from?: string | null;
  use_tls?: boolean;
  use_ssl?: boolean;
}

/**
 * POST /alerts/smtp-config — verify SMTP relay credentials, save them to
 * backend/.env, apply them immediately and send a test message to the signed-in
 * user. Unlike a Resend free-tier key, an SMTP relay delivers to ANY login email,
 * so this is the transport that makes per-account alert email work.
 *
 * Throws ApiError 400 when the relay rejects the credentials.
 */
export function saveSmtpConfig(
  data: SmtpConfig,
  signal?: AbortSignal,
): Promise<EmailConfig> {
  return request<EmailConfig>('/alerts/smtp-config', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/* ------------------------------------------------------------------ *
 * auth
 * ------------------------------------------------------------------ */

/**
 * POST /auth/signup — create a Store and its owner account.
 *
 * Body: { store_name, store_location?, email, password }.
 * Returns **202 without a token**: the account exists, a verification code has
 * been emailed, and the address is not a login until that code is confirmed.
 */
export function signup(
  data: { store_name: string; store_location?: string | null; email: string; password: string },
  signal?: AbortSignal,
): Promise<SignupPendingResponse> {
  return request<SignupPendingResponse>('/auth/signup', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/login — the password step.
 *
 * Answers with a full session only for an `mfa_exempt` account (the demo login).
 * Everything else gets `mfa_required` (second factor) or `mfa_setup_required`
 * (enrol first), and those tokens are held in memory by useMfaSession.
 */
export function login(
  data: { email: string; password: string },
  signal?: AbortSignal,
): Promise<LoginResponse> {
  return request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/** POST /auth/verify-email — consume the emailed 6-digit code. */
export function verifyEmail(
  data: { email: string; code: string },
  signal?: AbortSignal,
): Promise<VerifyEmailResponse> {
  return request<VerifyEmailResponse>('/auth/verify-email', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/resend-verification — a fresh code.
 *
 * The server enforces a 60-second cooldown and answers 429 with
 * `code: "resend_cooldown"` inside it; the verify screen mirrors that countdown
 * so the button is not offered while it would be refused.
 */
export function resendVerification(
  data: { email: string },
  signal?: AbortSignal,
): Promise<AuthStatusResponse> {
  return request<AuthStatusResponse>('/auth/resend-verification', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/forgot-password — ask for a reset link.
 *
 * The server answers `202 {status: "reset_requested"}` for **every** address, known or
 * not, and sends the lookup and the mail as a background task. The response body is
 * therefore not evidence that an account exists, and the UI must not present it as one:
 * show the same wording either way.
 */
export function forgotPassword(
  email: string,
  signal?: AbortSignal,
): Promise<AuthStatusResponse> {
  return request<AuthStatusResponse>('/auth/forgot-password', {
    method: 'POST',
    body: JSON.stringify({ email }),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/reset-password — apply the token from the emailed link.
 *
 * Returns a status only: the server deliberately issues no access, challenge or setup
 * token here, because a reset proves the mailbox and not the second factor. Sign in
 * afterwards through the ordinary flow.
 *
 * A link that is unknown, expired, already used or superseded all answer `400` with
 * `code: "reset_token_invalid"`; too many attempts answer `429` with
 * `code: "password_reset_rate_limited"`.
 */
export function resetPassword(
  token: string,
  newPassword: string,
  signal?: AbortSignal,
): Promise<AuthStatusResponse> {
  return request<AuthStatusResponse>('/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ token, new_password: newPassword }),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/mfa/verify — exchange a challenge token plus a TOTP (or recovery)
 * code for a real access token.
 *
 * The challenge token travels in the body, as the API defines it.
 */
export function mfaVerify(
  data: { challenge_token: string; code: string },
  signal?: AbortSignal,
): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>('/auth/mfa/verify', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/mfa/setup/start — issue a pending authenticator secret.
 *
 * Authenticated by the in-memory `mfa_setup` token, not by a session.
 */
export function mfaSetupStart(
  setupToken: string,
  signal?: AbortSignal,
): Promise<MfaSetupStartResponse> {
  return request<MfaSetupStartResponse>('/auth/mfa/setup/start', {
    method: 'POST',
    headers: { Authorization: `Bearer ${setupToken}` },
    signal,
  });
}

/**
 * POST /auth/mfa/setup/confirm — enable MFA and return the recovery codes.
 *
 * This is the only response that ever contains the plaintext codes; they cannot be
 * retrieved again, which is why the UI insists they are saved before continuing.
 */
export function mfaSetupConfirm(
  setupToken: string,
  data: { code: string },
  signal?: AbortSignal,
): Promise<MfaSetupConfirmResponse> {
  return request<MfaSetupConfirmResponse>('/auth/mfa/setup/confirm', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${setupToken}`,
    },
    signal,
  });
}

/**
 * POST /auth/mfa/recovery-codes/regenerate — replace all recovery codes.
 *
 * Requires the session **and** both factors (password + current TOTP code), so a
 * stolen session alone cannot mint itself a permanent way in.
 */
export function regenerateRecoveryCodes(
  data: { password: string; code: string },
  signal?: AbortSignal,
): Promise<{ status: string; recovery_codes: string[] }> {
  return request<{ status: string; recovery_codes: string[] }>(
    '/auth/mfa/recovery-codes/regenerate',
    {
      method: 'POST',
      body: JSON.stringify(data),
      headers: { 'Content-Type': 'application/json' },
      signal,
    },
  );
}

/**
 * GET /auth/me — confirm the current bearer token is still valid.
 *
 * Returns the authenticated User (id, email, store_id), or throws ApiError 401
 * when the token is missing/expired/invalid. Used on app load to decide whether
 * the stored token is still good.
 */
export function getMe(signal?: AbortSignal): Promise<User> {
  return request<User>('/auth/me', { signal });
}

/* ------------------------------------------------------------------ *
 * stores / cameras
 * ------------------------------------------------------------------ */

/** GET /stores — every store, ordered by id. Bare array, not paginated. */
export function listStores(signal?: AbortSignal): Promise<Store[]> {
  return request<Store[]>('/stores', { signal });
}

/**
 * POST /stores — create a new store.
 *
 * Returns the created store with its assigned id. Throws ApiError 422 when
 * required fields are missing/invalid.
 */
export function createStore(
  data: { name: string; location?: string | null },
  signal?: AbortSignal,
): Promise<Store> {
  return request<Store>('/stores', { method: 'POST', body: JSON.stringify(data), headers: { 'Content-Type': 'application/json' }, signal });
}

/** GET /cameras — every camera, ordered by id. Bare array, not paginated. */
export function listCameras(signal?: AbortSignal): Promise<Camera[]> {
  return request<Camera[]>('/cameras', { signal });
}

/**
 * POST /cameras — create a new camera in the authenticated user's store.
 *
 * `aisle` and `name` are required. `store_id` is accepted for backwards
 * compatibility but ignored: the backend always scopes the camera to the
 * signed-in user's store. Throws ApiError 422 when required fields are
 * missing/invalid.
 */
export function createCamera(
  data: { store_id?: number; aisle: string; name: string },
  signal?: AbortSignal,
): Promise<Camera> {
  return request<Camera>('/cameras', { method: 'POST', body: JSON.stringify(data), headers: { 'Content-Type': 'application/json' }, signal });
}
