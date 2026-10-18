import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Bell,
  CheckCircle2,
  Loader2,
  Mail,
  RefreshCw,
  Send,
} from 'lucide-react';

import { describeError, isAbortError } from '../api/errors';
import {
  getEmailConfig,
  listAlerts,
  saveEmailConfig,
  saveSmtpConfig,
  sendTestAlert,
  type Alert as AlertRecord,
  type EmailConfig,
} from '../api/client';

/** The backend caps `limit` at 200; sorting and filtering run over what we hold. */
const ALERTS_LIMIT = 200;

const URGENCY_STYLES: Record<string, string> = {
  low: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  high: 'bg-red-50 text-red-700 border-red-200',
};

/** Sort order for urgency — highest first when sorting descending. */
const URGENCY_RANK: Record<string, number> = { low: 1, medium: 2, high: 3 };

function urgencyClass(urgency: string | null): string {
  return URGENCY_STYLES[urgency?.toLowerCase() ?? ''] ?? 'bg-gray-50 text-gray-600 border-gray-200';
}

/**
 * When the alert is considered to have happened: the Slack delivery time, or the
 * report's own timestamp when delivery never succeeded (`sent_at` is nullable).
 */
function alertTime(alert: AlertRecord): string | null {
  return alert.sent_at ?? alert.report_created_at;
}

function timeValue(alert: AlertRecord): number {
  const iso = alertTime(alert);
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function locationLabel(alert: AlertRecord): string {
  const parts = [alert.store_name, alert.aisle].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : `Report #${alert.shelf_report_id}`;
}

type SortKey = 'time' | 'urgency' | 'aisle';
type SortDir = 'asc' | 'desc';

/* ------------------------------------------------------------------ *
 * Send test alert
 * ------------------------------------------------------------------ */

type TestState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'sent'; detail: string; slack?: string; email?: string; recipients?: string[] }
  | { kind: 'error'; message: string };

/** A per-channel outcome chip: green when delivered, amber when switched off. */
function ChannelStatus({ label, state, hint }: { label: string; state?: string; hint?: string }) {
  if (!state) return null;
  const delivered = state === 'sent';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
        delivered
          ? 'border-green-200 bg-green-50 text-green-700'
          : 'border-amber-200 bg-amber-50 text-amber-700'
      }`}
      title={hint}
    >
      {label}:
      {delivered ? 'sent' : state === 'not_configured' ? 'not configured' : 'failed'}
      {hint ? ` — ${hint}` : ''}
    </span>
  );
}

function SendTestAlertButton() {
  const [state, setState] = useState<TestState>({ kind: 'idle' });

  async function handleClick() {
    if (state.kind === 'sending') return;
    setState({ kind: 'sending' });
    try {
      const result = await sendTestAlert();
      setState({
        kind: 'sent',
        detail: result.detail,
        slack: result.slack,
        email: result.email,
        recipients: result.email_recipients,
      });
    } catch (err) {
      if (isAbortError(err)) return;
      setState({ kind: 'error', message: describeError(err) });
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={state.kind === 'sending'}
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
      >
        {state.kind === 'sending' ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Send className="h-3.5 w-3.5" />
        )}
        {state.kind === 'sending' ? 'Sending…' : 'Send test alert'}
      </button>

      {state.kind === 'sent' && (
        <div className="flex flex-col items-end gap-1">
          <span className="inline-flex items-center gap-1 text-xs text-green-700">
            <CheckCircle2 className="h-3.5 w-3.5" />
            {state.detail}
          </span>
          {/* Say which channels actually carried the message — "sent" on Slack alone
              must not look like email worked. */}
          <span className="flex flex-wrap items-center justify-end gap-1">
            <ChannelStatus label="Slack" state={state.slack} />
            <ChannelStatus
              label="Email"
              state={state.email}
              hint={
                state.email === 'sent'
                  ? state.recipients?.join(', ')
                  : state.email === 'not_configured'
                    ? 'Add a Resend key with Email setup (or set SMTP_HOST + SMTP_FROM)'
                    : 'Check the backend logs for the provider response'
              }
            />
          </span>
        </div>
      )}
      {state.kind === 'error' && (
        <span className="inline-flex max-w-xs items-start gap-1 text-right text-xs text-red-700">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="break-words">{state.message}</span>
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Email setup
 * ------------------------------------------------------------------ */

type SetupState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved'; detail: string; key: string | null; delivered: string[]; error: string | null }
  | { kind: 'error'; message: string };

/** Which transport the operator is configuring: Resend API key or an SMTP relay. */
type Transport = 'smtp' | 'api';

const INPUT_CLASS =
  'w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500';

/**
 * Choose a transport and have the backend verify it with a real send before saving.
 *
 * SMTP (a Gmail App Password) is the default because it delivers to EVERY signed-in
 * account's login email — a Resend free-tier key only reaches the Resend account's own
 * address. Both are proven with a test message to the caller's login address before
 * anything is written to backend/.env, so a rejected credential is reported right here.
 */
function EmailSetupButton() {
  const [open, setOpen] = useState(false);
  const [transport, setTransport] = useState<Transport>('smtp');
  // Resend API key fields
  const [apiKey, setApiKey] = useState('');
  const [fromEmail, setFromEmail] = useState('');
  const [alertTo, setAlertTo] = useState('');
  // SMTP fields
  const [smtpHost, setSmtpHost] = useState('smtp.gmail.com');
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUser, setSmtpUser] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [smtpSsl, setSmtpSsl] = useState(false);
  const [state, setState] = useState<SetupState>({ kind: 'idle' });
  const [config, setConfig] = useState<EmailConfig | null>(null);
  const [configLoaded, setConfigLoaded] = useState(false);

  // Check whether email is already saved so we show "Configured" instead of an empty
  // form. This is the fix for "it asks for the API key again and again": once a
  // transport is in backend/.env, every account sees the configured state.
  useEffect(() => {
    const controller = new AbortController();
    getEmailConfig(controller.signal)
      .then((cfg) => {
        setConfig(cfg);
        if (cfg.alert_email_to) setAlertTo(cfg.alert_email_to);
        if (cfg.transport === 'smtp' && cfg.smtp_host) {
          setTransport('smtp');
          setSmtpHost(cfg.smtp_host);
          if (cfg.smtp_user) setSmtpUser(cfg.smtp_user);
        }
      })
      .catch(() => {})
      .finally(() => setConfigLoaded(true));
    return () => controller.abort();
  }, []);

  const isConfigured = configLoaded && config?.configured === true;
  const activeTransport = config?.transport;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (state.kind === 'saving') return;
    setState({ kind: 'saving' });
    try {
      if (transport === 'smtp') {
        if (!smtpUser.trim() || !smtpPassword.trim()) {
          setState({ kind: 'error', message: 'SMTP username and password are required.' });
          return;
        }
        const result = await saveSmtpConfig({
          host: smtpHost.trim() || 'smtp.gmail.com',
          port: smtpPort,
          user: smtpUser.trim(),
          password: smtpPassword,
          smtp_from: null,
          use_tls: !smtpSsl,
          use_ssl: smtpSsl,
        });
        setState({
          kind: 'saved',
          detail: result.detail ?? 'SMTP relay verified',
          key: null,
          delivered: result.email_delivered_to,
          error: result.last_error,
        });
        setConfig(result);
        setSmtpPassword('');
        setOpen(false);
        return;
      }
      if (!apiKey.trim()) {
        setState({ kind: 'error', message: 'Paste an API key first.' });
        return;
      }
      const result = await saveEmailConfig({
        api_key: apiKey.trim(),
        from_email: fromEmail.trim() || null,
        alert_email_to: alertTo.trim() || null,
      });
      setState({
        kind: 'saved',
        detail: result.detail ?? 'key verified',
        key: result.key,
        delivered: result.email_delivered_to,
        error: result.last_error,
      });
      setConfig(result);
      setApiKey('');
      setOpen(false);
    } catch (err) {
      if (isAbortError(err)) return;
      setState({ kind: 'error', message: describeError(err) });
    }
  }

  return (
    <div className="relative flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50"
      >
        <Mail className="h-3.5 w-3.5" />
        {isConfigured ? 'Email configured' : 'Email setup'}
      </button>

      {open && (
        <form
          onSubmit={handleSubmit}
          className="absolute right-0 top-9 z-20 w-80 rounded-lg border border-gray-200 bg-white p-3 text-left shadow-lg"
        >
          {isConfigured && (
            <p className="mb-2 flex items-center gap-1 rounded-md bg-emerald-50 p-2 text-[11px] text-emerald-700">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
              {activeTransport === 'smtp'
                ? `SMTP relay ${config?.smtp_host ?? ''} is saved. Enter new credentials to replace it.`
                : `Key ${config?.key ?? ''} is already saved. Enter a new key to replace it.`}
            </p>
          )}

          {/* Transport toggle — SMTP first because it reaches every login email. */}
          <div className="mb-2 flex rounded-md border border-gray-200 p-0.5 text-[11px] font-semibold">
            <button
              type="button"
              onClick={() => setTransport('smtp')}
              className={`flex-1 rounded px-2 py-1 transition ${
                transport === 'smtp' ? 'bg-sky-600 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              SMTP (Gmail)
            </button>
            <button
              type="button"
              onClick={() => setTransport('api')}
              className={`flex-1 rounded px-2 py-1 transition ${
                transport === 'api' ? 'bg-sky-600 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              Resend API
            </button>
          </div>

          {transport === 'smtp' ? (
            <>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-gray-700">
                  Email account (Gmail)
                </span>
                <input
                  type="text"
                  value={smtpUser}
                  onChange={(e) => setSmtpUser(e.target.value)}
                  placeholder="you@gmail.com"
                  autoComplete="off"
                  spellCheck={false}
                  className={INPUT_CLASS}
                />
              </label>
              <label className="mt-2 block">
                <span className="mb-1 block text-xs font-semibold text-gray-700">
                  App Password
                </span>
                <input
                  type="password"
                  value={smtpPassword}
                  onChange={(e) => setSmtpPassword(e.target.value)}
                  placeholder="16-char Google App Password"
                  autoComplete="off"
                  spellCheck={false}
                  className={INPUT_CLASS}
                />
              </label>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-gray-700">Host</span>
                  <input
                    type="text"
                    value={smtpHost}
                    onChange={(e) => setSmtpHost(e.target.value)}
                    className={INPUT_CLASS}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-semibold text-gray-700">Port</span>
                  <input
                    type="number"
                    value={smtpPort}
                    onChange={(e) => setSmtpPort(Number(e.target.value) || 587)}
                    className={INPUT_CLASS}
                  />
                </label>
              </div>
              <label className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-600">
                <input
                  type="checkbox"
                  checked={smtpSsl}
                  onChange={(e) => setSmtpSsl(e.target.checked)}
                  className="h-3 w-3"
                />
                Use implicit SSL (port 465) instead of STARTTLS (587)
              </label>
              <p className="mt-2 text-[11px] leading-snug text-gray-500">
                SMTP delivers to <strong>every</strong> signed-in account's login email — no
                domain or paid plan needed. For Gmail, turn on 2-Step Verification then create
                an App Password at{' '}
                <a
                  href="https://myaccount.google.com/apppasswords"
                  target="_blank"
                  rel="noreferrer"
                  className="font-semibold text-sky-600 hover:underline"
                >
                  myaccount.google.com/apppasswords
                </a>
                .
              </p>
            </>
          ) : (
            <>
              <label className="block">
                <span className="mb-1 block text-xs font-semibold text-gray-700">
                  Resend API key
                </span>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isConfigured ? 'Enter a new key to replace…' : 're_…'}
                  autoComplete="off"
                  spellCheck={false}
                  className={INPUT_CLASS}
                />
              </label>
              <label className="mt-2 block">
                <span className="mb-1 block text-xs font-semibold text-gray-700">
                  From address <span className="font-normal text-gray-400">(optional)</span>
                </span>
                <input
                  type="text"
                  value={fromEmail}
                  onChange={(e) => setFromEmail(e.target.value)}
                  placeholder="ShelfMonitor <alerts@yourdomain.com>"
                  className={INPUT_CLASS}
                />
              </label>
              <label className="mt-2 block">
                <span className="mb-1 block text-xs font-semibold text-gray-700">
                  Send alerts to <span className="font-normal text-gray-400">(optional)</span>
                </span>
                <input
                  type="text"
                  value={alertTo}
                  onChange={(e) => setAlertTo(e.target.value)}
                  placeholder="alerts@yourdomain.com"
                  className={INPUT_CLASS}
                />
                <span className="mt-0.5 block text-[10px] text-gray-400">
                  Resend free tier only delivers to your Resend account's own address — use
                  SMTP to reach every login email.
                </span>
              </label>
              <p className="mt-2 text-[11px] leading-snug text-gray-500">
                Create a free key at{' '}
                <a
                  href="https://resend.com/api-keys"
                  target="_blank"
                  rel="noreferrer"
                  className="font-semibold text-sky-600 hover:underline"
                >
                  resend.com/api-keys
                </a>
                .
              </p>
            </>
          )}

          {state.kind === 'error' && (
            <p className="mt-2 flex items-start gap-1 rounded-md bg-red-50 p-2 text-[11px] leading-snug text-red-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-words">{state.message}</span>
            </p>
          )}
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-md px-2 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={state.kind === 'saving'}
              className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:opacity-60"
            >
              {state.kind === 'saving' && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {state.kind === 'saving' ? 'Verifying…' : 'Save & send test'}
            </button>
          </div>
        </form>
      )}

      {state.kind === 'saved' && (
        <span
          className={`inline-flex max-w-xs items-start gap-1 text-right text-xs ${
            state.delivered.length > 0 ? 'text-green-700' : 'text-amber-700'
          }`}
        >
          {state.delivered.length > 0 ? (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : (
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          )}
          <span className="break-words">
            {state.delivered.length > 0
              ? `${state.key ? `Key ${state.key} saved` : 'SMTP relay saved'} (${state.detail}). Test email delivered to ${state.delivered.join(', ')}.`
              : `${state.key ? `Key ${state.key} saved` : 'SMTP relay saved'} and verified, but the test email was not delivered: ${state.error ?? 'check the backend logs'}`}
          </span>
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Sortable column header
 * ------------------------------------------------------------------ */

interface SortHeaderProps {
  label: string;
  column: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  className?: string;
}

function SortHeader({ label, column, sortKey, sortDir, onSort, className }: SortHeaderProps) {
  const active = sortKey === column;
  return (
    <th scope="col" className={`px-3 py-2 text-left font-semibold ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 transition hover:text-gray-900 ${
          active ? 'text-gray-900' : 'text-gray-500'
        }`}
        aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        {label}
        {active &&
          (sortDir === 'asc' ? (
            <ArrowUp className="h-3 w-3" />
          ) : (
            <ArrowDown className="h-3 w-3" />
          ))}
      </button>
    </th>
  );
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export default function Alerts() {
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [urgencyFilter, setUrgencyFilter] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('time');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      // GET /alerts answers a Page envelope — the rows are in `items`.
      const page = await listAlerts({ limit: ALERTS_LIMIT }, signal);
      setAlerts(page.items);
      setTotal(page.total);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(describeError(err));
      setAlerts([]);
      setTotal(0);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  /** Urgency values actually present, so the filter never offers a dead option. */
  const urgencyOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const a of alerts) if (a.urgency) seen.add(a.urgency);
    return [...seen].sort((a, b) => (URGENCY_RANK[b] ?? 0) - (URGENCY_RANK[a] ?? 0));
  }, [alerts]);

  const visible = useMemo(() => {
    // Date inputs are local calendar days; widen `to` to the end of that day.
    const fromMs = fromDate ? new Date(`${fromDate}T00:00:00`).getTime() : null;
    const toMs = toDate ? new Date(`${toDate}T23:59:59.999`).getTime() : null;

    const filtered = alerts.filter((a) => {
      if (urgencyFilter && a.urgency !== urgencyFilter) return false;
      if (fromMs !== null || toMs !== null) {
        const t = timeValue(a);
        if (!t) return false;
        if (fromMs !== null && t < fromMs) return false;
        if (toMs !== null && t > toMs) return false;
      }
      return true;
    });

    const direction = sortDir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      let diff: number;
      if (sortKey === 'time') {
        diff = timeValue(a) - timeValue(b);
      } else if (sortKey === 'urgency') {
        diff = (URGENCY_RANK[a.urgency ?? ''] ?? 0) - (URGENCY_RANK[b.urgency ?? ''] ?? 0);
      } else {
        diff = locationLabel(a).localeCompare(locationLabel(b));
      }
      // Keep ordering stable when the sort key ties.
      return diff !== 0 ? diff * direction : (a.id - b.id) * direction;
    });
  }, [alerts, urgencyFilter, fromDate, toDate, sortKey, sortDir]);

  const filtersActive = Boolean(urgencyFilter || fromDate || toDate);

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Alerts</h1>
          <p className="text-xs text-gray-500">
            {loading
              ? 'Loading alerts…'
              : `${visible.length} of ${alerts.length} loaded${
                  total > alerts.length ? ` (${total} total)` : ''
                }`}
          </p>
        </div>
        <div className="flex items-start gap-2">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <EmailSetupButton />
          <SendTestAlertButton />
        </div>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-3">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">Urgency</span>
          <select
            value={urgencyFilter}
            onChange={(e) => setUrgencyFilter(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs capitalize text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          >
            <option value="">All</option>
            {urgencyOptions.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">From</span>
          <input
            type="date"
            value={fromDate}
            max={toDate || undefined}
            onChange={(e) => setFromDate(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">To</span>
          <input
            type="date"
            value={toDate}
            min={fromDate || undefined}
            onChange={(e) => setToDate(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </label>

        {filtersActive && (
          <button
            type="button"
            onClick={() => {
              setUrgencyFilter('');
              setFromDate('');
              setToDate('');
            }}
            className="rounded-md px-2 py-1.5 text-xs font-semibold text-sky-600 transition hover:bg-sky-50"
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Could not load alerts: <span className="break-words font-semibold">{error}</span>
          </span>
        </div>
      )}

      {loading && alerts.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-8 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading alerts…
        </div>
      )}

      {!loading && !error && alerts.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center">
          <Bell className="mx-auto h-6 w-6 text-gray-400" />
          <p className="mt-2 text-sm font-semibold text-gray-900">No alerts yet</p>
          <p className="mt-1 text-xs text-gray-500">
            An alert is raised for each high-urgency shelf report — delivered to Slack and
            emailed to your store's login address.
          </p>
        </div>
      )}

      {alerts.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full text-xs">
            <thead className="border-b border-gray-200 bg-gray-50 text-gray-500">
              <tr>
                <SortHeader
                  label="Time"
                  column="time"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                  className="whitespace-nowrap"
                />
                <SortHeader
                  label="Store / aisle"
                  column="aisle"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                  className="whitespace-nowrap"
                />
                <SortHeader
                  label="Urgency"
                  column="urgency"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                />
                <th scope="col" className="px-3 py-2 text-left font-semibold text-gray-500">
                  Summary
                </th>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-gray-500">
                  Slack
                </th>
                <th scope="col" className="px-3 py-2 text-left font-semibold text-gray-500">
                  Email
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {visible.map((alert) => (
                <tr key={alert.id} className="align-top transition hover:bg-gray-50">
                  <td className="whitespace-nowrap px-3 py-2 text-gray-700">
                    {formatTimestamp(alertTime(alert))}
                    {!alert.sent_at && alert.report_created_at && (
                      <span className="block text-[11px] text-gray-400">report time</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className="font-medium text-gray-900">{locationLabel(alert)}</span>
                    {alert.camera_name && (
                      <span className="block text-[11px] text-gray-400">{alert.camera_name}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded-full border px-2 py-0.5 font-semibold capitalize ${urgencyClass(
                        alert.urgency,
                      )}`}
                    >
                      {alert.urgency ?? 'unknown'}
                    </span>
                  </td>
                  <td className="max-w-md px-3 py-2 leading-relaxed text-gray-600">
                    {alert.llm_summary ?? (
                      <span className="italic text-gray-400">No summary recorded.</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {alert.sent_to_slack ? (
                      <span className="inline-flex items-center gap-1 text-green-700">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Sent
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-gray-500">
                        <AlertCircle className="h-3.5 w-3.5" />
                        Not sent
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    {alert.sent_to_email ? (
                      <span
                        className="inline-flex items-center gap-1 text-green-700"
                        title={
                          alert.email_recipients
                            ? `Sent to ${alert.email_recipients}`
                            : 'Sent to the store\u2019s login address'
                        }
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Sent
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-gray-500">
                        <AlertCircle className="h-3.5 w-3.5" />
                        Not sent
                      </span>
                    )}
                  </td>
                </tr>
              ))}

              {visible.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-gray-500">
                    No alerts match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {total > alerts.length && (
        <p className="text-xs text-gray-400">
          Showing the {alerts.length} most recent alerts of {total}; sorting and filtering apply to
          those.
        </p>
      )}
    </section>
  );
}
