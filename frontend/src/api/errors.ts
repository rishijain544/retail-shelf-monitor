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
