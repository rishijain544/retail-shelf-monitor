import { ApiError } from './client';

/**
 * True when a rejection is just a cancelled request — an unmounted component or
 * a superseded call — rather than a real failure worth showing the user.
 *
 * Checks `name` structurally instead of `instanceof DOMException` so it also
 * catches the AbortError that waitForCaptureReport() raises from its own timer.
 */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'AbortError'
  );
}

/**
 * True when the request never produced an HTTP response at all — the browser
 * rejects fetch() with a TypeError for CORS failures, DNS errors, offline and
 * dropped connections, so there is no status code to report. The exact wording
 * is browser-specific (Chrome "Failed to fetch", Firefox "NetworkError when
 * attempting to fetch resource.", Safari "Load failed").
 */
export function isNetworkError(error: unknown): boolean {
  if (!(error instanceof TypeError)) return false;
  return /failed to fetch|networkerror|load failed|network request failed/i.test(error.message);
}

/** Human-readable message for a failed request, preferring FastAPI's `detail`. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  if (isNetworkError(error)) {
    return 'The server could not be reached, so the request never completed. Check your connection and try again.';
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

/**
 * True for the backend's temporary lockouts: too many wrong passwords
 * (`account_locked`) or too many wrong second factors (`mfa_locked`).
 *
 * Both are `429`; branching on the machine-readable `code` rather than the message
 * keeps this working if the wording changes.
 */
export function isLockoutError(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 429) return false;
  return error.code === 'account_locked' || error.code === 'mfa_locked';
}

/** "Too many attempts. Try again in N minutes." with N parsed from the server. */
const _MINUTES = /(\d+)\s*minute/i;

function _lockoutMinutes(error: ApiError): number {
  const match = _MINUTES.exec(error.detail ?? '');
  const parsed = match ? Number(match[1]) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 15;
}

/**
 * Message for an auth failure, preferring something actionable over a bare status.
 *
 * Sign-in and verification errors are the ones a person can act on, so they are
 * normalised here rather than repeated in every screen. Everything else falls
 * through to `describeError`.
 */
export function describeAuthError(error: unknown): string {
  if (isLockoutError(error)) {
    const minutes = _lockoutMinutes(error as ApiError);
    return `Too many attempts. Try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`;
  }
  if (error instanceof ApiError && error.code === 'mfa_challenge_invalid') {
    return 'That sign-in attempt has expired. Sign in again to start over.';
  }
  if (error instanceof ApiError && error.code === 'email_unverified') {
    return 'Your email address is not verified yet. Enter the code we emailed you.';
  }
  return describeError(error);
}
