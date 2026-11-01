import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, Loader2, MailCheck } from 'lucide-react';

import { ApiError, forgotPassword } from '../api/client';
import { describeError } from '../api/errors';
import AuthShell from '../components/AuthShell';

/**
 * Mirrors `PASSWORD_RESET_COOLDOWN_SECONDS` on the server. The backend enforces the
 * window itself and still answers 202 inside it, so this countdown exists only so the
 * button is not offered while a resend would be silently dropped.
 */
const RESEND_SECONDS = 60;

/**
 * One sentence for every outcome, because the backend deliberately cannot tell the
 * browser whether the address has an account. Anything derived from the response body
 * would turn this screen into an account-existence oracle.
 */
const SENT_MESSAGE =
  "If an account exists for that email, we've sent a reset link. It expires in 30 minutes";

/**
 * Step 1 of a password reset: ask for the link.
 *
 * No session, no token, and no state in storage — a visitor can leave at any point and
 * the only thing they need is the email that arrives.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  // One interval per active second; the state change clears and restarts it, which keeps
  // the cleanup simple and stops at zero (same pattern as resend-on-verify).
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => {
      setCooldown((seconds) => Math.max(0, seconds - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const send = async () => {
    setPending(true);
    setError(null);
    try {
      await forgotPassword(email);
      // Success and "no such account" are the same 202, so there is nothing to branch on.
      setSent(true);
      setCooldown(RESEND_SECONDS);
    } catch (err) {
      // A per-IP budget can outlast the account cooldown; saying so is honest and says
      // nothing about the address. Everything else (offline, server down) is a real
      // failure the visitor can act on.
      if (err instanceof ApiError && (err.status === 429 || err.code === 'password_reset_rate_limited')) {
        setError('Too many reset requests from this connection. Please wait a few minutes and try again.');
      } else {
        setError(describeError(err));
      }
    } finally {
      setPending(false);
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!email) {
      setError('Enter the email address for your account.');
      return;
    }
    await send();
  };

  return (
    <AuthShell
      title="Forgot your password?"
      subtitle="Enter the email you use for ShelfMonitor and we'll send you a link to set a new password."
      leftHeading="Get back in"
      leftBody="A reset link lets you choose a new password. Your shelf reports, alerts and two-factor settings stay exactly as they are."
      footer={
        <Link to="/login" className="font-semibold text-sky-600 hover:text-sky-700">
          Back to login
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

        {sent && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-lg bg-sky-50 border border-sky-200 text-sky-800 text-sm px-4 py-3"
          >
            <MailCheck className="h-4 w-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <span>
              {SENT_MESSAGE}. Check your inbox and spam folder.
            </span>
          </div>
        )}

        <div>
          <label
            htmlFor="forgot-email"
            className="block text-sm font-semibold text-gray-700 mb-1.5"
          >
            Email
          </label>
          <input
            id="forgot-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@store.example"
            autoComplete="email"
            required
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
          />
        </div>

        {sent ? (
          <button
            type="button"
            onClick={send}
            disabled={pending || cooldown > 0}
            className="w-full rounded-lg border border-gray-300 bg-white text-gray-700 font-semibold px-4 py-2.5 text-sm hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend link'}
          </button>
        ) : (
          <button
            type="submit"
            disabled={pending}
            className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {pending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Sending…
              </>
            ) : (
              'Send reset link'
            )}
          </button>
        )}
      </form>
    </AuthShell>
  );
}
