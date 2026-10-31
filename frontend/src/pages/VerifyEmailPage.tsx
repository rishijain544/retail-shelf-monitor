import { useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { AlertCircle, Loader2, MailCheck } from 'lucide-react';

import { resendVerification, verifyEmail } from '../api/client';
import { describeAuthError, describeError } from '../api/errors';
import AuthShell from '../components/AuthShell';
import NumericCodeInput from '../components/NumericCodeInput';
import { beginMfaSetup } from '../hooks/useMfaSession';

/** Mirrors the server's resend cooldown so the button is not offered too early. */
const RESEND_SECONDS = 60;

/**
 * Step 2 of signup: prove the address.
 *
 * Signup deliberately returns no session, so this screen is the only way forward.
 * The email comes from the router state written by SignupPage — it is not a secret,
 * but it is enough that a direct visit (or a refresh) has nothing to verify, which
 * sends the visitor back to the login screen rather than showing a dead form.
 */
export default function VerifyEmailPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const email = (location.state as { email?: string } | null)?.email;

  const [code, setCode] = useState('');
  const [pending, setPending] = useState<'verify' | 'resend' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(RESEND_SECONDS);

  // One interval per active second; the state change clears and restarts it, which
  // keeps the cleanup simple and stops at zero.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => {
      setCooldown((seconds) => Math.max(0, seconds - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  if (!email) return <Navigate to="/login" replace />;

  const handleVerify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (code.length !== 6) {
      setError('Enter the 6-digit code from the email.');
      return;
    }
    setPending('verify');
    setError(null);
    setNotice(null);
    try {
      const body = await verifyEmail({ email, code });
      // A verified address still has no session: enrolment is mandatory next.
      beginMfaSetup(email, body.setup_token, body.expires_in);
      navigate('/login/mfa-setup', { replace: true });
    } catch (err) {
      setError(describeAuthError(err));
    } finally {
      setPending(null);
    }
  };

  const handleResend = async () => {
    if (cooldown > 0) return;
    setPending('resend');
    setError(null);
    setNotice(null);
    try {
      await resendVerification({ email });
      setCooldown(RESEND_SECONDS);
      setNotice('A new code is on its way.');
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPending(null);
    }
  };

  return (
    <AuthShell
      title="Verify your email"
      subtitle={`We emailed a 6-digit code to ${email}. Enter it to activate your account.`}
      leftHeading="One last step"
      leftBody="Confirming your address protects your store alerts — nobody can sign up with an email they don't control."
      footer={
        <button
          type="button"
          onClick={() => navigate('/signup', { replace: true })}
          className="font-semibold text-sky-600 hover:text-sky-700"
        >
          Use a different email
        </button>
      }
    >
      <form onSubmit={handleVerify} className="space-y-5">
        {error && (
          <div
            role="alert"
            className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3"
          >
            <AlertCircle className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}
        {notice && (
          <div className="flex items-center gap-2 rounded-lg bg-sky-50 border border-sky-200 text-sky-800 text-sm px-4 py-3">
            <MailCheck className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span>{notice}</span>
          </div>
        )}

        <NumericCodeInput
          id="verify-code"
          label="Verification code"
          value={code}
          onChange={setCode}
          hint="The code expires 10 minutes after it was sent."
          disabled={pending === 'verify'}
        />

        <button
          type="submit"
          disabled={pending !== null}
          className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          {pending === 'verify' ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Verifying…
            </>
          ) : (
            'Verify email'
          )}
        </button>

        <button
          type="button"
          onClick={handleResend}
          disabled={pending !== null || cooldown > 0}
          className="w-full rounded-lg border border-gray-300 bg-white text-gray-700 font-semibold px-4 py-2.5 text-sm hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
        >
          {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
        </button>
      </form>
    </AuthShell>
  );
}
