import { useCallback, useEffect, useState } from 'react';

import { getHealth, type HealthData } from '../api/client';

/** How often the health endpoint is re-polled, in ms. */
const POLL_INTERVAL_MS = 10000;

/**
 * Hard cap on a single /health request. Keeps it well under POLL_INTERVAL_MS so
 * a hung backend surfaces as an error rather than leaving the cards on a stale
 * "Connecting..." state forever.
 */
const REQUEST_TIMEOUT_MS = 8000;

export interface UseHealthResult {
  health: HealthData | null;
  loading: boolean;
  error: string | null;
  /** Re-fetch immediately (used by the "Refresh Health" button). */
  refresh: () => void;
}

/**
 * Poll GET /health every 10s, exposing the latest result plus a manual refresh.
 *
 * This is the health-check logic previously inlined in App.tsx, unchanged in
 * behaviour — it now lives in the persistent layout so every route shows it.
 */
export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const data = await getHealth(controller.signal);
      setHealth(data);
    } catch (e) {
      console.error('Error fetching health status:', e);
      const message =
        e instanceof DOMException && e.name === 'AbortError'
          ? `Health check timed out after ${REQUEST_TIMEOUT_MS}ms`
          : e instanceof Error
            ? e.message
            : String(e);
      setError(message);
      setHealth(null);
    } finally {
      clearTimeout(timer);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, POLL_INTERVAL_MS); // poll every 10s
    return () => clearInterval(interval);
  }, [fetchHealth]);

  return { health, loading, error, refresh: fetchHealth };
}
