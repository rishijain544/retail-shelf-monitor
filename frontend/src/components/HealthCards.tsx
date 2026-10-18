import { Database, Flame, Mail } from 'lucide-react';

import type { HealthData } from '../api/client';

interface HealthCardsProps {
  health: HealthData | null;
  /** Non-null when the last /health request failed outright. */
  error: string | null;
}

/** Tri-state summary of one service, derived from its health string. */
type ServiceState = 'healthy' | 'down' | 'unknown';

/**
 * Map a backend service status string to a display state.
 *
 * The backend reports either "healthy", "unknown", or "unhealthy: <reason>".
 * Anything that is neither "healthy" nor "unknown" is a definite failure — it
 * must not be rendered as "Connecting..." (which is what previously made Redis
 * look stuck forever when it was actually down).
 */
function serviceState(value: string | null | undefined): ServiceState {
  if (value === 'healthy') return 'healthy';
  if (!value || value === 'unknown') return 'unknown';
  return 'down';
}

const BADGE_STYLES: Record<ServiceState, string> = {
  healthy: 'bg-emerald-100 text-emerald-800',
  down: 'bg-red-100 text-red-800',
  unknown: 'bg-amber-100 text-amber-800',
};

const BADGE_LABELS: Record<ServiceState, string> = {
  healthy: 'Healthy',
  down: 'Down',
  unknown: 'Connecting...',
};

function StatusBadge({ state, detail }: { state: ServiceState; detail?: string | null }) {
  return (
    <span
      title={detail ?? undefined}
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${BADGE_STYLES[state]}`}
    >
      {BADGE_LABELS[state]}
    </span>
  );
}

/**
 * The service-status cards (FastAPI / PostgreSQL / alert email).
 *
 * Rendered by AppLayout, so it stays visible on every route.
 */
export default function HealthCards({ health, error }: HealthCardsProps) {
  // When the /health request itself failed we have no per-service data; the
  // backend/dependency cards should say so instead of idling on "Connecting...".
  const dependencyState = (value: string | null | undefined): ServiceState =>
    error ? 'down' : serviceState(value);

  const dbValue = health?.services?.database;
  const dbState = dependencyState(dbValue);

  // Email alerting is either working, configured but failing, or not set up at all.
  // The backend appends the reason to an "unhealthy: …" status, which is exactly
  // what the operator needs to see when alerts are raised but no mail leaves.
  const emailStatus = health?.services?.alert_email;
  const emailProblem =
    typeof emailStatus === 'string' && emailStatus.startsWith('unhealthy:')
      ? emailStatus.slice('unhealthy:'.length).trim()
      : null;
  const emailConfigured = emailStatus === 'healthy';
  const emailTransport = health?.services?.alert_email_transport;
  const emailHint = error
    ? error
    : emailProblem
      ? emailProblem
      : emailConfigured
        ? `Alerts are emailed through the ${emailTransport === 'api' ? 'email API key' : 'SMTP relay'} transport.`
        : 'No email transport yet: add a Resend API key from the Alerts page (Email setup), or set SMTP_HOST + SMTP_FROM in backend/.env.';

  return (
    <section>
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">
        System Service Integration
      </h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

        {/* FastAPI Status */}
        <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-200 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-lg bg-emerald-50 text-emerald-600">
              <Flame className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-semibold text-sm text-gray-700">FastAPI Backend</h3>
              <p className="text-xs text-gray-400">REST API Engine</p>
            </div>
          </div>
          <div>
            {error ? (
              <span
                title={error}
                className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800"
              >
                Disconnected
              </span>
            ) : (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800">
                Connected
              </span>
            )}
          </div>
        </div>

        {/* PostgreSQL Status */}
        <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-200 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-lg bg-sky-50 text-sky-600">
              <Database className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-semibold text-sm text-gray-700">PostgreSQL DB</h3>
              <p className="text-xs text-gray-400">Relational Storage</p>
            </div>
          </div>
          <div>
            <StatusBadge state={dbState} detail={error ?? dbValue} />
          </div>
        </div>

        {/* Alert email delivery — makes a missing transport visible before an alert is
            silently recorded as "email not sent". */}
        <div className="bg-white p-4 rounded-xl shadow-sm border border-gray-200 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-lg bg-violet-50 text-violet-600">
              <Mail className="h-5 w-5" />
            </div>
            <div>
              <h3 className="font-semibold text-sm text-gray-700">Alert Email</h3>
              <p className="text-xs text-gray-400">High-urgency notifications</p>
            </div>
          </div>
          <div>
            {error ? (
              <StatusBadge state="down" detail={error} />
            ) : (
              <span
                title={emailHint}
                className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${
                  emailConfigured
                    ? 'bg-emerald-100 text-emerald-800'
                    : emailProblem
                      ? 'bg-red-100 text-red-800'
                      : 'bg-amber-100 text-amber-800'
                }`}
              >
                {emailConfigured ? 'Configured' : emailProblem ? 'Not working' : 'Not configured'}
              </span>
            )}
          </div>
        </div>

      </div>
    </section>
  );
}
