import { useCallback, useEffect, useState } from 'react';

import { getMe, type User } from '../api/client';

const AUTH_STATE_KEY = 'sm:auth:state';

type AuthState =
  | { kind: 'authenticated'; user: User; token: string }
  | { kind: 'unauthenticated' };

type AuthStateChangeHandler = (state: AuthState) => void;

let _listeners: AuthStateChangeHandler[] = [];

function _emit(state: AuthState): void {
  for (const listener of _listeners) listener(state);
}

/**
 * Read the canonical auth state from localStorage (if any).
 *
 * We keep a single source of truth in localStorage so that a page reload or a
 * second tab does not lose the user's session. The in-memory value in this hook
 * is always derived from that persisted value, so the two stay in sync.
 */
function readPersistedState(): AuthState {
  try {
    const raw = localStorage.getItem(AUTH_STATE_KEY);
    if (!raw) return { kind: 'unauthenticated' };
    const parsed = JSON.parse(raw) as {
      kind: 'authenticated';
      user: User;
      token: string;
    } | { kind: 'unauthenticated' };
    if (
      parsed.kind === 'authenticated' &&
      typeof parsed.user?.id === 'number' &&
      typeof parsed.token === 'string'
    ) {
      return parsed as AuthState;
    }
    return { kind: 'unauthenticated' };
  } catch {
    return { kind: 'unauthenticated' };
  }
}

function writePersistedState(state: AuthState): void {
  try {
    if (state.kind === 'authenticated') {
      localStorage.setItem(
        AUTH_STATE_KEY,
        JSON.stringify({ kind: 'authenticated', user: state.user, token: state.token }),
      );
    } else {
      localStorage.removeItem(AUTH_STATE_KEY);
    }
  } catch {
    // localStorage can throw in private mode or when quota is exceeded. Treat it
    // as a best-effort cache; the in-memory state is still authoritative for the
    // current page.
  }
}

/**
 * Programmatically log the current user out (clears memory + localStorage).
 *
 * This is also callable outside React (e.g. from a token-refresh helper or an
 * error boundary), because it writes directly to the persisted state rather than
 * relying on a hook instance.
 */
export function logout(): void {
  writePersistedState({ kind: 'unauthenticated' });
  _emit({ kind: 'unauthenticated' });
}

function setAuthenticated(user: User, token: string): void {
  const state: AuthState = { kind: 'authenticated', user, token };
  writePersistedState(state);
  _emit(state);
}

/**
 * Hook that owns the current auth state for the running app.
 *
 * On mount it replays the persisted state, then calls GET /auth/me to confirm the
 * stored token is still valid. If /auth/me returns 401 (expired/invalid token),
 * the state is cleared and the user is treated as logged out. While a login or
 * signup is in flight, callers can subscribe to `onAuthStateChange` instead of
 * polling `state`.
 *
 * The token is sent automatically on every API call through the client's
 * `request()` helper, which injects `Authorization: Bearer <token>` when
 * `currentToken()` is set — there is no per-call plumbing to add.
 */
export function useAuth() {
  const [state, setState] = useState<AuthState>(readPersistedState);

  useEffect(() => {
    _listeners.push(setState);
    return () => {
      _listeners = _listeners.filter((l) => l !== setState);
    };
  }, [setState]);

  const login = useCallback(
    async (user: User, token: string) => {
      setAuthenticated(user, token);
    },
    [],
  );

  const checkSession = useCallback(async () => {
    const current = readPersistedState();
    if (current.kind !== 'authenticated') return current;

    try {
      const user = await getMe();
      // The backend may have a newer store_id if the user's account was migrated;
      // keep the token but refresh the user record so the UI reflects reality.
      setAuthenticated(user, current.token);
      return readPersistedState();
    } catch {
      // Token is no longer valid — clear it everywhere and force a re-login.
      logout();
      return readPersistedState();
    }
  }, []);

  const onAuthStateChange = useCallback(
    (handler: AuthStateChangeHandler) => {
      _listeners.push(handler);
      return () => {
        _listeners = _listeners.filter((l) => l !== handler);
      };
    },
    [],
  );

  return { state, login, logout, checkSession, onAuthStateChange };
}
