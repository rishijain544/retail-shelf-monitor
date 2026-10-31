import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { QRCodeSVG } from 'qrcode.react';
import { AlertCircle, Check, Copy, Loader2, Smartphone } from 'lucide-react';

import {
  ApiError,
  mfaSetupConfirm,
  mfaSetupStart,
  type MfaSetupStartResponse,
  type User,
} from '../api/client';
import { describeAuthError } from '../api/errors';
import AuthShell from '../components/AuthShell';
import NumericCodeInput from '../components/NumericCodeInput';
import RecoveryCodesPanel from '../components/RecoveryCodesPanel';
import { clearPendingFlow, useMfaSession } from '../hooks/useMfaSession';
import { useAuth } from '../hooks/useAuth';

type Confirmed = { user: User; token: string; codes: string[] };

/** Numbered step marker; the flow is strictly ordered, so the numbering is real. */
function StepBadge({ step }: { step: number }) {
  return (
    <span
      className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-sky-100 text-xs font-bold text-sky-700"
      aria-hidden="true"
    >
      {step}
    </span>
  );
}

/**
 * Mandatory two-factor enrolment.
 *
 * Reached from either a fresh signup (after the address is verified) or a login on
 * an account that has never enrolled. There is no "skip": the backend issues no
 * access token until this finishes, so the only way out is `/login`.
 *
 * The QR code is rendered in the browser from the `otpauth://` URI — the secret is
 * never sent to a QR-image service, and it is never stored anywhere on the client
 * beyond this component's state.
 */
export default function MfaSetupPage() {
  const navigate = useNavigate();
  const { login: setAuth } = useAuth();
  const pending = useMfaSession();

  const [start, setStart] = useState<MfaSetupStartResponse | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState<Confirmed | null>(null);
  const [copied, setCopied] = useState(false);
  const [finished, setFinished] = useState(false);

  const setupToken = pending?.kind === 'setup' ? pending.token : null;

  useEffect(() => {
    if (!setupToken) return;
    const controller = new AbortController();
    let cancelled = false;

    mfaSetupStart(setupToken, controller.signal).then(
      (body) => {
        if (!cancelled) setStart(body);
      },
      (err: unknown) => {
        if (cancelled) return;
        // A 401 here means the 15-minute setup token is gone; there is nothing this
        // page can do except send the visitor back to sign in.
        setStartError(
          err instanceof ApiError && err.status === 401
            ? 'Your setup session expired. Sign in again to continue.'
            : describeAuthError(err),
        );
      },
    );

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [setupToken]);

  // After Continue the flow is over; render nothing rather than bouncing through
  // the "no pending token" redirect and landing on /login.
  if (!pending || pending.kind !== 'setup') {
    return finished ? null : <Navigate to="/login" replace />;
  }

  const copySecret = async (secret: string) => {
    try {
      await navigator.clipboard?.writeText(secret);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be denied; the key is selectable on screen either way.
    }
  };

  const handleConfirm = async (event: React.FormEvent) => {
    event.preventDefault();
    if (code.length !== 6) {
      setError('Enter the 6-digit code shown in your authenticator app.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = await mfaSetupConfirm(pending.token, { code });
      // Not a session yet: the recovery codes must be acknowledged first.
      setConfirmed({ user: body.user, token: body.access_token, codes: body.recovery_codes });
    } catch (err) {
      setError(describeAuthError(err));
      setCode('');
    } finally {
      setSubmitting(false);
    }
  };

  const finish = () => {
    if (!confirmed) return;
    setFinished(true);
    setAuth(confirmed.user, confirmed.token);
    clearPendingFlow();
    navigate('/', { replace: true });
  };

  if (confirmed) {
    return (
      <AuthShell
        title="Save your recovery codes"
        subtitle="You can use any of these instead of an authenticator code."
        leftHeading="Two-factor is on"
        leftBody="From now on, signing in needs your password and a code from your authenticator app."
      >
        <RecoveryCodesPanel codes={confirmed.codes} onContinue={finish} />
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Set up two-factor authentication"
      subtitle="This step is required before you can use your account."
      leftHeading="Protect your store"
      leftBody="A second factor means a stolen password is not enough to reach your shelf reports and alerts."
      footer={
        <Link to="/login" className="font-semibold text-sky-600 hover:text-sky-700">
          Sign in with a different account
        </Link>
      }
    >
      <ol className="space-y-6">
        <li className="flex gap-3">
          <StepBadge step={1} />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-900">Install an authenticator app</p>
            <p className="mt-1 flex items-center gap-1.5 text-sm text-gray-600">
              <Smartphone className="h-4 w-4 flex-shrink-0 text-gray-400" aria-hidden="true" />
              Google Authenticator, Microsoft Authenticator, 1Password or Authy — any TOTP app
              works.
            </p>
          </div>
        </li>

        <li className="flex gap-3">
          <StepBadge step={2} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-gray-900">Scan the QR code</p>
            <p className="mt-1 text-sm text-gray-600">
              Or type the key into the app by hand.
            </p>

            {startError && (
              <div
                role="alert"
                className="mt-3 flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3"
              >
                <AlertCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                <span>{startError}</span>
              </div>
            )}

            {!start && !startError && (
              <p className="mt-3 flex items-center gap-2 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Preparing your key…
              </p>
            )}

            {start && (
              <div className="mt-3 space-y-3">
                <div className="inline-flex rounded-lg border border-gray-200 bg-white p-3">
                  <QRCodeSVG
                    value={start.otpauth_uri}
                    size={168}
                    title="QR code for your authenticator app"
                  />
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Manual entry key
                  </p>
                  <div className="mt-1 flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-800">
                      {start.secret}
                    </code>
                    <button
                      type="button"
                      onClick={() => copySecret(start.secret)}
                      className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-colors"
                    >
                      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </li>

        <li className="flex gap-3">
          <StepBadge step={3} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-gray-900">Confirm the code</p>
            <form onSubmit={handleConfirm} className="mt-3 space-y-4">
              {error && (
                <div
                  role="alert"
                  className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3"
                >
                  <AlertCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              )}

              <NumericCodeInput
                id="setup-code"
                label="Code from your app"
                value={code}
                onChange={setCode}
                disabled={submitting || !start}
              />

              <button
                type="submit"
                disabled={submitting || !start}
                className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Turning on…
                  </>
                ) : (
                  'Turn on two-factor authentication'
                )}
              </button>
            </form>
          </div>
        </li>
      </ol>
    </AuthShell>
  );
}
