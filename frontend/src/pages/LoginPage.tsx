import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldCheck, Loader2, AlertCircle, Sparkles } from 'lucide-react';

import { login, type AuthTokenResponse } from '../api/client';
import { describeError } from '../api/errors';
import { DEMO_EMAIL, DEMO_PASSWORD } from '../demo';
import { useAuth } from '../hooks/useAuth';

/** Which control started the in-flight sign-in, so only it shows a spinner. */
type PendingSource = 'form' | 'demo' | null;

export default function LoginPage() {
  const navigate = useNavigate();
  const { login: setAuth } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState<PendingSource>(null);
  const [error, setError] = useState<string | null>(null);

  const loading = pending !== null;

  /** Shared by the form submit and the one-click demo sign-in. */
  const signIn = async (
    credentials: { email: string; password: string },
    source: Exclude<PendingSource, null>,
  ) => {
    setPending(source);
    setError(null);
    try {
      const body: AuthTokenResponse = await login(credentials);
      setAuth(body.user, body.access_token);
      navigate('/', { replace: true });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPending(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError('Both email and password are required.');
      return;
    }
    await signIn({ email, password }, 'form');
  };

  /**
   * Fill the form with the demo account and submit it, so a first-time visitor
   * gets a populated dashboard in one click (and can see which account is used).
   */
  const handleDemoLogin = async () => {
    setEmail(DEMO_EMAIL);
    setPassword(DEMO_PASSWORD);
    await signIn({ email: DEMO_EMAIL, password: DEMO_PASSWORD }, 'demo');
  };

  return (
    <div className="min-h-screen flex">
      {/* Left panel — brand */}
      <div className="hidden md:flex md:w-1/2 bg-sky-600 flex-col justify-center px-10 text-white">
        <div className="flex items-center gap-3 mb-6">
          <div className="bg-white/20 p-2 rounded-lg">
            <ShieldCheck className="h-8 w-8" />
          </div>
          <div className="text-xl font-bold">ShelfMonitor</div>
        </div>
        <h2 className="text-3xl font-bold leading-tight mb-4">
          Welcome back
        </h2>
        <p className="text-white/80 text-lg max-w-md">
          Sign in to your store account to view shelf reports, alerts, and trends.
        </p>
      </div>

      {/* Right panel — form */}
      <div className="w-full md:w-1/2 flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <div className="md:hidden flex items-center gap-3 mb-6">
            <div className="bg-sky-600 text-white p-2 rounded-lg">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div className="text-xl font-bold text-gray-900">ShelfMonitor</div>
          </div>

          <h1 className="text-2xl font-bold text-gray-900 mb-2">Sign in</h1>
          <p className="text-sm text-gray-500 mb-8">
            Enter your account email and password.
          </p>

          <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
              <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-4 py-3">
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <div>
              <label
                htmlFor="login-email"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Email
              </label>
              <input
                id="login-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@store.example"
                required
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Password
              </label>
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Your password"
                required
                minLength={1}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {pending === 'form' ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Signing in…
                </>
              ) : (
                'Sign in'
              )}
            </button>

            {/* One-click way in for visitors: no credentials to look up. */}
            <div>
              <div className="relative mb-4">
                <div className="absolute inset-0 flex items-center" aria-hidden="true">
                  <div className="w-full border-t border-gray-200" />
                </div>
                <div className="relative flex justify-center">
                  <span className="bg-white px-2 text-xs uppercase tracking-wide text-gray-400">
                    or
                  </span>
                </div>
              </div>

              <button
                type="button"
                onClick={handleDemoLogin}
                disabled={loading}
                className="flex items-center justify-center gap-2 w-full rounded-lg border border-sky-200 bg-sky-50 text-sky-700 font-semibold px-4 py-2.5 text-sm hover:bg-sky-100 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
              >
                {pending === 'demo' ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Signing in…
                  </>
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" />
                    Try Demo Account
                  </>
                )}
              </button>

              <p className="mt-3 text-center text-xs text-gray-500">
                Demo credentials:{' '}
                <span className="font-mono text-gray-700">{DEMO_EMAIL}</span>
                {' / '}
                <span className="font-mono text-gray-700">{DEMO_PASSWORD}</span>
              </p>
            </div>
          </form>

          <p className="mt-6 text-center text-sm text-gray-500">
            Don't have an account?{' '}
            <a
              href="/signup"
              className="font-semibold text-sky-600 hover:text-sky-700"
            >
              Create one
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
