/**
 * frontend/src/demo.ts
 *
 * Credentials for the demo account created by `backend/scripts/seed.py`, which
 * seeds one store ("Downtown Flagship Store") with historical captures, reports
 * and alerts.
 *
 * These credentials are intentionally public: the login page offers a one-click
 * "Try Demo Account" sign-in so a visitor can see a populated dashboard without
 * reading any setup documentation. They only grant access to seeded demo data.
 *
 * The defaults must stay in sync with DEMO_EMAIL / DEMO_PASSWORD in the seed
 * script. Override them with VITE_DEMO_EMAIL / VITE_DEMO_PASSWORD for a
 * deployment whose demo account uses different values.
 */
export const DEMO_EMAIL: string = import.meta.env.VITE_DEMO_EMAIL || 'demo@shelfmonitor.test';

export const DEMO_PASSWORD: string = import.meta.env.VITE_DEMO_PASSWORD || 'demo-2026-shelf';
