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
 * The authenticated user's own record, as returned by POST /auth/signup,
 * POST /auth/login, and GET /auth/me.
 */
export interface User {
  id: number;
  email: string;
  store_id: number;
}

/**
 * Response shared by POST /auth/signup and POST /auth/login: a bearer token
 * plus the user it belongs to.
 */
export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
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

  constructor(status: number, detail: string | null, url: string) {
    super(detail ?? `Request to ${url} failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
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

/** Pull FastAPI's {"detail": ...} out of an error body, tolerating non-JSON. */
async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === 'string') return detail;
    if (detail !== undefined) return JSON.stringify(detail);
    return null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const mergedInit: RequestInit = {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(activeAuthHeaders() ?? {}),
    },
  };
  const response = await fetch(url, mergedInit);
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorDetail(response), url);
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
 * POST /auth/signup — create a Store and its owner account, return a JWT.
 *
 * Body: { store_name, store_location?, email, password }.
 * Returns AuthTokenResponse with `access_token` + the new `user`.
 */
export function signup(
  data: { store_name: string; store_location?: string | null; email: string; password: string },
  signal?: AbortSignal,
): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>('/auth/signup', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
}

/**
 * POST /auth/login — authenticate with email+password, return a JWT.
 *
 * Body: { email, password }. Returns AuthTokenResponse with `access_token` +
 * the authenticated `user`.
 */
export function login(
  data: { email: string; password: string },
  signal?: AbortSignal,
): Promise<AuthTokenResponse> {
  return request<AuthTokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify(data),
    headers: { 'Content-Type': 'application/json' },
    signal,
  });
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
