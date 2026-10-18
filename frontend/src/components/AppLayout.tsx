import { useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  ShieldCheck,
  RefreshCw,
  LayoutDashboard,
  Bell,
  TrendingUp,
  Settings,
  LogOut,
  Store as StoreIcon,
} from 'lucide-react';

import { listStores } from '../api/client';
import { useHealth } from '../hooks/useHealth';
import { useAuth } from '../hooks/useAuth';
import HealthCards from './HealthCards';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/trends', label: 'Trends', icon: TrendingUp },
  { to: '/admin', label: 'Directory', icon: Settings },
];

/**
 * Persistent shell for every route: header, sidebar nav, health cards, and a
 * top-right account menu with a Log out action.
 *
 * Health polling lives here (via useHealth) so it keeps running across
 * navigation instead of restarting per page.
 *
 * Log out clears the JWT (in memory and localStorage) and redirects to /login.
 * The bearer token is sent automatically on every API call through
 * `src/api/client.ts`'s `request()` helper, so there is no per-call header
 * plumbing.
 *
 * The header also names the store the session is scoped to, so it is always
 * clear whose data is on screen.
 */
export default function AppLayout() {
  const navigate = useNavigate();
  const { state, logout: doLogout } = useAuth();
  const { health, loading, error, refresh } = useHealth();

  const handleLogout = () => {
    doLogout();
    navigate('/login', { replace: true });
  };

  const accountName = state.kind === 'authenticated' ? state.user.email : null;
  const storeId = state.kind === 'authenticated' ? state.user.store_id : null;
  const [storeName, setStoreName] = useState<string | null>(null);

  // Show the store whose data is on screen. The name comes from GET /stores;
  // fall back to the id so the header is never vague, and never let a directory
  // failure break the shell.
  useEffect(() => {
    if (storeId === null) return;
    const controller = new AbortController();
    listStores(controller.signal).then(
      (stores) => {
        const match = stores.find((s) => s.id === storeId);
        setStoreName(match?.name ?? `Store ${storeId}`);
      },
      () => setStoreName(`Store ${storeId}`),
    );
    return () => controller.abort();
  }, [storeId]);

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="bg-sky-600 text-white p-2 rounded-lg">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-gray-900">ShelfMonitor</h1>
                {storeName && (
                  <span
                    className="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs font-semibold text-sky-700"
                    title={`Store: ${storeName}`}
                  >
                    <StoreIcon className="h-3 w-3" aria-hidden="true" />
                    {storeName}
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500 font-medium">
                Restocking &amp; Shelf Management Assistant
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Account indicator */}
            {accountName && (
              <div className="hidden sm:flex items-center gap-2 text-sm">
                <span className="text-gray-500">Signed in as</span>
                <span className="font-semibold text-gray-900 max-w-[200px] truncate" title={accountName}>
                  {accountName}
                </span>
              </div>
            )}

            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-colors"
              aria-label="Log out"
            >
              <LogOut className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Log out</span>
            </button>

            <button
              onClick={refresh}
              className="flex items-center space-x-1.5 px-3 py-1.5 border border-gray-300 rounded-md text-xs font-semibold text-gray-600 bg-white hover:bg-gray-50 transition-colors"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              <span>Refresh Health</span>
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 flex flex-col md:flex-row gap-8">
        {/* Sidebar nav */}
        <nav aria-label="Main navigation" className="md:w-56 md:flex-shrink-0">
          <ul className="flex md:flex-col gap-1">
            {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
              <li key={to} className="flex-1 md:flex-none">
                <NavLink
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `flex items-center space-x-2 px-3 py-2 rounded-lg text-sm font-semibold transition-colors ${
                      isActive
                        ? 'bg-sky-50 text-sky-700'
                        : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                    }`
                  }
                >
                  <Icon className="h-4 w-4" />
                  <span>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Route content, under the always-visible health cards */}
        <main className="flex-1 min-w-0 space-y-8">
          <HealthCards health={health} error={error} />
          <Outlet />
        </main>
      </div>

      {/* Footer */}
      <footer className="bg-white border-t border-gray-200 py-6 mt-auto">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center text-xs text-gray-400 font-medium">
          Retail Shelf Monitoring &amp; Restocking Assistant &copy;{' '}
          {new Date().getFullYear()} - Managed with Docker Compose, FastAPI, React, and YOLOv8
        </div>
      </footer>
    </div>
  );
}
