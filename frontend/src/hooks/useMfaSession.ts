import { useEffect, useState } from 'react';

/**
 * frontend/src/hooks/useMfaSession.ts
 *
 * The half-finished sign-in: a correct password (or a verified email) produced a
 * short-lived `mfa_challenge` or `mfa_setup` token, and the account is still not
 * logged in.
 *
 * These tokens live in a module variable **only**. They are not a session, so
 * putting them in localStorage would be wrong twice over: a reload would resume a
 * state the user never finished (and the server would refuse anyway once the token
 * expires), and the token would outlive the tab on a shared machine. A refresh
 * therefore restarts at the login screen, which is exactly what the flow should do.
 *
 * The store is deliberately tiny (a value + listeners) so the pages that use it do
 * not need a state library; `useAuth` uses the same pattern for the real session.
 */

export type PendingFlow =
  | {
      kind: 'challenge';
      email: string;
      /** Short-lived token from POST /auth/login — memory only. */
      token: string;
      /** Epoch ms; after this the server will refuse the token. */
      expiresAt: number;
    }
  | {
      kind: 'setup';
      email: string;
      /** Short-lived token from POST /auth/login or /auth/verify-email — memory only. */
      token: string;
      expiresAt: number;
    };

let _pending: PendingFlow | null = null;
let _listeners: Array<(flow: PendingFlow | null) => void> = [];

function _now(): number {
  return Date.now();
}

function _emit(): void {
  for (const listener of _listeners) listener(_pending);
}

function _secondsToMs(seconds: number | undefined): number {
  // The API always sends `expires_in`; fall back to the documented lifetimes
  // (5 min challenge, 15 min setup) rather than treating the token as immortal.
  const fallback = 5 * 60;
  return Math.max(0, (seconds ?? fallback) * 1000);
}

/** Start (or restart) the second-factor step. */
export function beginMfaChallenge(
  email: string,
  token: string,
  expiresInSeconds?: number,
): void {
  _pending = {
    kind: 'challenge',
    email,
    token,
    expiresAt: _now() + _secondsToMs(expiresInSeconds),
  };
  _emit();
}

/** Start (or restart) the mandatory enrolment step. */
export function beginMfaSetup(email: string, token: string, expiresInSeconds?: number): void {
  _pending = {
    kind: 'setup',
    email,
    token,
    expiresAt: _now() + _secondsToMs(expiresInSeconds),
  };
  _emit();
}

/**
 * The pending flow, or null when there is none (or it has expired).
 *
 * Expired entries are dropped on read: a stale token would only produce a 401 and a
 * confusing error message after the user typed a code.
 */
export function getPendingFlow(): PendingFlow | null {
  if (_pending !== null && _pending.expiresAt <= _now()) {
    // Drop it silently: this also runs during render (useState initialiser), and an
    // emit there would set state on other components mid-render.
    _pending = null;
  }
  return _pending;
}

/** Forget the pending flow (finished, abandoned, or the token was rejected). */
export function clearPendingFlow(): void {
  if (_pending === null) return;
  _pending = null;
  _emit();
}

/** Subscribe to / read the pending flow from a component. */
export function useMfaSession(): PendingFlow | null {
  const [flow, setFlow] = useState<PendingFlow | null>(getPendingFlow);

  useEffect(() => {
    _listeners.push(setFlow);
    // Re-read on mount: the flow may have started between useState and this effect.
    setFlow(getPendingFlow());
    return () => {
      _listeners = _listeners.filter((listener) => listener !== setFlow);
    };
  }, []);

  return flow;
}
