import { Suspense, lazy, useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import AppLayout from './components/AppLayout';
import { useAuth } from './hooks/useAuth';
import Dashboard from './pages/Dashboard';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import ShelfDetail from './pages/ShelfDetail';
import Alerts from './pages/Alerts';
import Admin from './pages/Admin';
import LoginPage from './pages/LoginPage';
import MfaCodePage from './pages/MfaCodePage';
import MfaSetupPage from './pages/MfaSetupPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import Security from './pages/Security';
import SignupPage from './pages/SignupPage';
import VerifyEmailPage from './pages/VerifyEmailPage';

/**
 * Trends is the only page that pulls in Recharts, which is roughly two thirds of
 * the app's JavaScript. Splitting it out keeps that weight off the Dashboard —
 * the page everyone lands on — and loads it only when someone opens /trends.
 */
const Trends = lazy(() => import('./pages/Trends'));

function RouteFallback() {
  return (
    <div className="flex items-center gap-2 p-8 text-sm text-gray-600">
      <Loader2 className="h-4 w-4 animate-spin" />
      Loading…
    </div>
  );
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const { state, checkSession } = useAuth();

  useEffect(() => {
    // Confirm the persisted token is still valid on every mount of the gated
    // tree. This runs once per app load (the gated tree is mounted only when we
    // already think we are authenticated), and also after any login/signup that
    // sets the token.
    checkSession();
  }, [checkSession]);

  if (state.kind !== 'authenticated') {
    // Re-check on the next tick in case the auth state change is still settling.
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

/**
 * Route table.
 *
 * Routes under AppLayout require a valid session (checked via GET /auth/me on
 * load). Login and signup are standalone pages outside the main layout so they
 * have no sidebar nav and no health cards.
 */
export default function App() {
  return (
    <Routes>
      {/*
       * Standalone auth pages — no AppLayout shell.
       *
       * /login/mfa and /login/mfa-setup are the two half-finished states: the
       * challenge and setup tokens live in memory only (useMfaSession), so these
       * pages redirect to /login when there is no pending flow — including after a
       * refresh, which deliberately restarts the sign-in.
       */}
      <Route path="/login" element={<LoginPage />} />
      {/*
       * Public for the obvious reason: these exist for people who cannot sign in. They
       * never consult useMfaSession, so a half-finished sign-in (challenge/setup token
       * in memory) cannot pull them away, and sitting outside AuthGate means a stale
       * session cannot redirect them either.
       */}
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/login/mfa" element={<MfaCodePage />} />
      <Route path="/login/mfa-setup" element={<MfaSetupPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage />} />

      {/* Authenticated app shell */}
      <Route
        element={
          <AuthGate>
            <AppLayout />
          </AuthGate>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="reports/:id" element={<ShelfDetail />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="admin" element={<Admin />} />
        <Route path="security" element={<Security />} />
        <Route
          path="trends"
          element={
            <Suspense fallback={<RouteFallback />}>
              <Trends />
            </Suspense>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
