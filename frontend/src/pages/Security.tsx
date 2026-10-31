import { useState } from 'react';
import { AlertCircle, CheckCircle2, KeyRound, Loader2, ShieldCheck, ShieldOff } from 'lucide-react';

import { regenerateRecoveryCodes } from '../api/client';
import { describeAuthError } from '../api/errors';
import { DEMO_EMAIL } from '../demo';
import RecoveryCodesPanel from '../components/RecoveryCodesPanel';
import { useAuth } from '../hooks/useAuth';

/**
 * Account security: what protects the account, and the one maintenance action that
 * exists for it.
 *
 * There is deliberately no "turn MFA off" button — the backend has no endpoint for
 * it either, so removing a second factor can never be done from a stolen session.
 * Replacing the recovery codes needs the password *and* a current authenticator
 * code for the same reason.
 */
export default function Security() {
  const { state } = useAuth();
  const user = state.kind === 'authenticated' ? state.user : null;

  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);

  const mfaEnabled = user?.mfa_enabled === true;
  // The demo account is the one exemption: it has no mailbox and no authenticator
  // the public could hold, so "Off" is expected rather than a setup reminder.
  const exempt = !mfaEnabled && user?.email === DEMO_EMAIL;

  const handleRegenerate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!password || !code) {
      setError('Enter your password and the current code from your authenticator app.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = await regenerateRecoveryCodes({ password, code });
      setNewCodes(body.recovery_codes);
      setPassword('');
      setCode('');
    } catch (err) {
      setError(describeAuthError(err));
      setCode('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-lg font-semibold text-gray-900">Security</h1>
        <p className="text-xs text-gray-500">
          How this account is protected, and how to replace its recovery codes.
        </p>
      </header>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
          Two-factor authentication
        </h2>

        <div className="flex items-start gap-3">
          {mfaEnabled ? (
            <CheckCircle2 className="mt-0.5 h-5 w-5 flex-shrink-0 text-emerald-600" aria-hidden="true" />
          ) : (
            <ShieldOff className="mt-0.5 h-5 w-5 flex-shrink-0 text-gray-400" aria-hidden="true" />
          )}
          <div>
            <p className="text-sm font-semibold text-gray-900">
              {mfaEnabled ? 'Two-factor authentication: On' : 'Two-factor authentication: Off'}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {mfaEnabled ? (
                <>
                  Signing in needs your password plus a code from your authenticator app. If you
                  lose the app, use one of your saved recovery codes.
                </>
              ) : exempt ? (
                <>
                  This is the shared demo account, so it signs in with a password only — there is
                  no mailbox or authenticator the public could hold. Any account you create must
                  enrol a second factor.
                </>
              ) : (
                <>
                  No authenticator is enrolled yet. It will be required the next time you sign in
                  to this account.
                </>
              )}
            </p>
            <p className="mt-2 text-xs text-gray-400">
              Signed in as <span className="font-mono text-gray-600">{user?.email}</span>
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-1">
          Recovery codes
        </h2>

        {newCodes ? (
          <div className="mt-3">
            <RecoveryCodesPanel
              codes={newCodes}
              onContinue={() => setNewCodes(null)}
              continueLabel="Done"
            />
          </div>
        ) : (
          <>
            <p className="mb-3 text-xs text-gray-500">
              Replacing them invalidates every code you saved earlier. Both your password and a
              current authenticator code are required.
            </p>

            {!mfaEnabled ? (
              <p className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
                This account has no authenticator, so it has no recovery codes. Sign in to an
                account with two-factor enabled to manage them.
              </p>
            ) : (
              <form onSubmit={handleRegenerate} className="space-y-4">
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
                    htmlFor="security-password"
                    className="block text-sm font-semibold text-gray-700 mb-1.5"
                  >
                    Password
                  </label>
                  <input
                    id="security-password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
                  />
                </div>

                <div>
                  <label
                    htmlFor="security-code"
                    className="block text-sm font-semibold text-gray-700 mb-1.5"
                  >
                    Authenticator code
                  </label>
                  <input
                    id="security-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={code}
                    onChange={(event) => setCode(event.target.value.replace(/[^0-9]/g, ''))}
                    maxLength={6}
                    placeholder="123456"
                    className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm tracking-widest text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
                  />
                </div>

                <button
                  type="submit"
                  disabled={submitting}
                  className="inline-flex items-center gap-2 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {submitting ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                      Regenerating…
                    </>
                  ) : (
                    <>
                      <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                      Regenerate recovery codes
                    </>
                  )}
                </button>
              </form>
            )}
          </>
        )}
      </section>

      <section className="rounded-lg border border-gray-200 bg-white p-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">
          <ShieldCheck className="h-4 w-4 text-sky-600" aria-hidden="true" />
          Lost your device and codes?
        </h2>
        <p className="text-xs text-gray-500">
          A second factor cannot be removed from this page — that is what makes it a second
          factor. An operator can clear the enrolment for one account with{' '}
          <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[11px] text-gray-700">
            python scripts/reset_mfa.py &lt;email&gt;
          </code>
          , after which the account enrols a new authenticator on its next sign-in.
        </p>
      </section>
    </section>
  );
}
