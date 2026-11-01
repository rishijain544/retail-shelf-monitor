import { useLayoutEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { AlertCircle, Eye, EyeOff, Loader2, LockKeyhole } from 'lucide-react';

import { ApiError, resetPassword } from '../api/client';
import { describeError } from '../api/errors';
import AuthShell from '../components/AuthShell';
import { MAX_PASSWORD_CHARS, PASSWORD_HINT, passwordError } from '../passwordRules';

/** Query parameter carrying the one-time token in the emailed link. */
const TOKEN_PARAM = 'token';

/** Shown on the login page after a reset; signing in is deliberately a separate step. */
export const PASSWORD_UPDATED_NOTICE = 'Password updated. Please log in.';

/** Same sentence the server uses, so a screenshot of either reads the same. */
const INVALID_LINK_MESSAGE =
  'That reset link is invalid or has expired. Request a new one to keep going.';

/**
 * Remove the reset token from the address bar and the current history entry.
 *
 * The token is single-use, but it stays usable until it is redeemed or superseded, so
 * leaving it in the URL would leak it through copy/paste, screenshots, bookmarks and
 * the back button. `replaceState` (not the router) is used so no new history entry is
 * created and React Router does not re-run its matching against a different URL.
 */
function stripTokenFromAddressBar(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has(TOKEN_PARAM)) return;
  url.searchParams.delete(TOKEN_PARAM);
  window.history.replaceState(
    window.history.state,
    '',
    `${url.pathname}${url.search}${url.hash}`,
  );
}

/**
 * Step 2 of a password reset: choose the new password.
 *
 * Public by design (it sits outside the auth guard): someone who cannot sign in is
 * exactly who uses it. The token is read once, kept in component state only — never in
 * localStorage/sessionStorage — and is not logged anywhere.
 *
 * Success sends the visitor to /login with a one-time notice instead of signing them
 * in: a reset proves the mailbox, not the second factor, so the ordinary flow (password,
 * then authenticator code) is the only thing that can open a session.
 */
export default function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();

  // Read once. `location.search` (not window.location) so this also works under a
  // MemoryRouter in tests; the layout effect below cleans the real address bar.
  const [token] = useState<string | null>(() => {
    const value = new URLSearchParams(location.search).get(TOKEN_PARAM);
    return value && value.trim() ? value.trim() : null;
  });

  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkRejected, setLinkRejected] = useState(false);

  // Before the first paint, so the token is never visible in the address bar.
  useLayoutEffect(() => {
    stripTokenFromAddressBar();
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token) return;

    const rule = passwordError(password);
    if (rule) {
      setError(rule);
      return;
    }
    if (password !== confirmation) {
      setError('The two passwords do not match.');
      return;
    }

    setPending(true);
    setError(null);
    try {
      await resetPassword(token, password);
      // No session is issued, so this does not sign the visitor in.
      navigate('/login', { replace: true, state: { notice: PASSWORD_UPDATED_NOTICE } });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'reset_token_invalid') {
        setLinkRejected(true);
        setError(INVALID_LINK_MESSAGE);
      } else if (
        err instanceof ApiError &&
        (err.status === 429 || err.code === 'password_reset_rate_limited')
      ) {
        setError('Too many attempts. Wait a few minutes, then open the link again.');
      } else {
        setError(describeError(err));
      }
    } finally {
      setPending(false);
    }
  };

  // A link with no token at all (hand-typed, truncated by a mail client) or one the
  // server has rejected both end here: one message plus the only useful next step.
  const unusableLink = token === null || linkRejected;

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="Set a new password for your ShelfMonitor account, then sign in with it."
      leftHeading="Almost there"
      leftBody="Saving a new password signs out every other session. Two-factor authentication stays on, so the next sign-in still asks for your authenticator code."
      // The unusable-link card carries its own call to action; two identical links
      // would be noise (and ambiguous for screen readers).
      footer={
        unusableLink ? undefined : (
          <Link to="/forgot-password" className="font-semibold text-sky-600 hover:text-sky-700">
            Request a new link
          </Link>
        )
      }
    >
      {unusableLink ? (
        <div className="space-y-5">
          <div
            role="alert"
            className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3"
          >
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <span>
              {token === null
                ? 'This link is missing its reset code. Request a new link to continue.'
                : INVALID_LINK_MESSAGE}
            </span>
          </div>
          <Link
            to="/forgot-password"
            className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 transition-colors"
          >
            Request a new link
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-5">
          {error && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3"
            >
              <AlertCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          <div>
            <label
              htmlFor="reset-password"
              className="block text-sm font-semibold text-gray-700 mb-1.5"
            >
              New password
            </label>
            <div className="relative">
              <input
                id="reset-password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 8 characters"
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={MAX_PASSWORD_CHARS}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 pr-11 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
              <button
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? 'Hide passwords' : 'Show passwords'}
                aria-controls="reset-password reset-password-confirm"
                className="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-600"
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Eye className="h-4 w-4" aria-hidden="true" />
                )}
              </button>
            </div>
            <p className="mt-1 text-xs text-gray-400">{PASSWORD_HINT}</p>
          </div>

          <div>
            <label
              htmlFor="reset-password-confirm"
              className="block text-sm font-semibold text-gray-700 mb-1.5"
            >
              Confirm new password
            </label>
            <input
              id="reset-password-confirm"
              type={showPassword ? 'text' : 'password'}
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
              placeholder="Type it again"
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={MAX_PASSWORD_CHARS}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
            />
          </div>

          <button
            type="submit"
            disabled={pending}
            className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {pending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Updating…
              </>
            ) : (
              <>
                <LockKeyhole className="h-4 w-4" aria-hidden="true" />
                Update password
              </>
            )}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
