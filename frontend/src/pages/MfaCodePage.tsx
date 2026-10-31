import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { AlertCircle, KeyRound, Loader2 } from 'lucide-react';

import { mfaVerify } from '../api/client';
import { describeAuthError } from '../api/errors';
import AuthShell from '../components/AuthShell';
import NumericCodeInput from '../components/NumericCodeInput';
import { clearPendingFlow, useMfaSession } from '../hooks/useMfaSession';
import { useAuth } from '../hooks/useAuth';

/**
 * Step 2 of login for an enrolled account.
 *
 * The password was accepted, but that only produced a 5-minute `mfa_challenge`
 * token in memory (`useMfaSession`) — not a session. Without that token this page
 * has nothing to send, so it redirects to /login instead of rendering a form whose
 * submit could only fail.
 */
export default function MfaCodePage() {
  const navigate = useNavigate();
  const { login: setAuth } = useAuth();
  const pending = useMfaSession();
  const [code, setCode] = useState('');
  const [recoveryMode, setRecoveryMode] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  // `finished` stops the guard below from bouncing a completed sign-in through
  // /login while the route change settles.
  if (!pending || pending.kind !== 'challenge') {
    return finished ? null : <Navigate to="/login" replace />;
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = code.trim();
    if (!trimmed) {
      setError(recoveryMode ? 'Enter one of your recovery codes.' : 'Enter the 6-digit code.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = await mfaVerify({ challenge_token: pending.token, code: trimmed });
      // Only now is this a real session: persist it, and drop the challenge token
      // from memory (it is never written to storage in the first place).
      setFinished(true);
      setAuth(body.user, body.access_token);
      clearPendingFlow();
      navigate('/', { replace: true });
    } catch (err) {
      setError(describeAuthError(err));
      setCode('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthShell
      title="Two-factor authentication"
      subtitle="Enter the 6-digit code from your authenticator app to finish signing in."
      leftHeading="Almost there"
      leftBody="A password alone is not enough for this account. Your authenticator app supplies the second factor."
      footer={
        <Link to="/login" className="font-semibold text-sky-600 hover:text-sky-700">
          Use a different account
        </Link>
      }
    >
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

        {recoveryMode ? (
          <div>
            <label
              htmlFor="recovery-code"
              className="block text-sm font-semibold text-gray-700 mb-1.5"
            >
              Recovery code
            </label>
            <input
              id="recovery-code"
              type="text"
              value={code}
              onChange={(event) => setCode(event.target.value)}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              placeholder="abcd-ef12"
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-center text-lg font-mono tracking-widest text-gray-900 placeholder-gray-300 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
            />
            <p className="mt-1.5 text-xs text-gray-400">
              Each recovery code works once. Case and dashes do not matter.
            </p>
          </div>
        ) : (
          <NumericCodeInput
            id="mfa-code"
            label="Authenticator code"
            value={code}
            onChange={setCode}
            disabled={submitting}
          />
        )}

        <button
          type="submit"
          disabled={submitting}
          className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Verifying…
            </>
          ) : (
            'Verify and sign in'
          )}
        </button>

        <button
          type="button"
          onClick={() => {
            setRecoveryMode((on) => !on);
            setCode('');
            setError(null);
          }}
          className="flex items-center justify-center gap-2 w-full text-sm font-semibold text-sky-600 hover:text-sky-700"
        >
          <KeyRound className="h-4 w-4" aria-hidden="true" />
          {recoveryMode ? 'Use an authenticator code instead' : 'Use a recovery code instead'}
        </button>
      </form>
    </AuthShell>
  );
}
