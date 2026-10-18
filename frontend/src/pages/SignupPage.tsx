import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldCheck, Loader2, AlertCircle } from 'lucide-react';

import { signup, type AuthTokenResponse } from '../api/client';
import { useAuth } from '../hooks/useAuth';

export default function SignupPage() {
  const navigate = useNavigate();
  const { login: setAuth } = useAuth();
  const [storeName, setStoreName] = useState('');
  const [storeLocation, setStoreLocation] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!storeName || !email || !password) {
      setError('Store name, email, and password are required.');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const body: AuthTokenResponse = await signup({
        store_name: storeName,
        store_location: storeLocation.trim() || null,
        email,
        password,
      });
      setAuth(body.user, body.access_token);
      navigate('/', { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Signup failed. Please try again.',
      );
    } finally {
      setLoading(false);
    }
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
          Create your store account
        </h2>
        <p className="text-white/80 text-lg max-w-md">
          Set up your store and owner account in one step — we will provision the
          store and create your admin account together.
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

          <h1 className="text-2xl font-bold text-gray-900 mb-2">Sign up</h1>
          <p className="text-sm text-gray-500 mb-8">
            Create your store and owner account.
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
                htmlFor="signup-store-name"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Store name
              </label>
              <input
                id="signup-store-name"
                type="text"
                value={storeName}
                onChange={(e) => setStoreName(e.target.value)}
                placeholder="Downtown Market"
                required
                minLength={1}
                maxLength={255}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
            </div>

            <div>
              <label
                htmlFor="signup-store-location"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Store location <span className="font-normal text-gray-400">(optional)</span>
              </label>
              <input
                id="signup-store-location"
                type="text"
                value={storeLocation}
                onChange={(e) => setStoreLocation(e.target.value)}
                placeholder="123 Main St, Anytown"
                maxLength={255}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
            </div>

            <div>
              <label
                htmlFor="signup-email"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Email
              </label>
              <input
                id="signup-email"
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
                htmlFor="signup-password"
                className="block text-sm font-semibold text-gray-700 mb-1.5"
              >
                Password
              </label>
              <input
                id="signup-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="At least 8 characters"
                required
                minLength={8}
                maxLength={72}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20"
              />
              <p className="mt-1 text-xs text-gray-400">
                Must be at least 8 characters.
              </p>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="flex items-center justify-center gap-2 w-full rounded-lg bg-sky-600 text-white font-semibold px-4 py-2.5 text-sm hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Creating account…
                </>
              ) : (
                'Create account'
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-gray-500">
            Already have an account?{' '}
            <a
              href="/login"
              className="font-semibold text-sky-600 hover:text-sky-700"
            >
              Sign in
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
