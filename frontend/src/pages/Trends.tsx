import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  LineChart as ChartIcon,
  Loader2,
  RefreshCw,
  Table2,
} from 'lucide-react';
import {
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { describeError, isAbortError } from '../api/errors';
import {
  listCameras,
  listReports,
  type Camera as CameraRecord,
  type ShelfReport,
} from '../api/client';

/** The backend caps `limit` at 200. */
const REPORTS_LIMIT = 200;

/**
 * Categorical series colours, in fixed slot order.
 *
 * These are not eyeballed: the set was run through the data-viz palette
 * validator against this chart's actual surface (#ffffff) and passes the
 * lightness band, chroma floor, adjacent-pair CVD separation (worst 9.1 protan)
 * and the normal-vision floor (worst 19.6). Three slots — aqua, yellow and
 * magenta — sit below 3:1 contrast on white, which obliges a non-colour way to
 * read every value; that is what the table view below is for. Slots are assigned
 * in this order and never cycled: a 9th aisle gets no colour (see `hidden`).
 */
export const SERIES_COLORS = [
  '#2a78d6', // 1 blue
  '#eb6834', // 2 orange
  '#1baf7a', // 3 aqua
  '#eda100', // 4 yellow
  '#e87ba4', // 5 magenta
  '#008300', // 6 green
  '#4a3aa7', // 7 violet
  '#e34948', // 8 red
] as const;

/** Chart chrome. Deliberately recessive — the data is the only loud thing. */
const SURFACE = '#ffffff';
const INK_PRIMARY = '#0b0b0b';
const INK_SECONDARY = '#52514e';
const INK_MUTED = '#898781';
const GRIDLINE = '#e1e0d9';
const AXIS = '#c3c2b7';

const CHART_HEIGHT = 360;

/**
 * Minimum gap, in percentage points, between two series' final values before
 * their end-labels are considered to collide. The y-domain is a fixed 0–100
 * over roughly 320px of plot, so ~18px of label box is ~5.6pp; 7 leaves margin.
 */
const END_LABEL_MIN_GAP = 7;

/** Dots become noise on a dense line; below this many rows they aid reading. */
const DOT_DENSITY_LIMIT = 30;

/** A chart row: the timestamp plus one fill-percentage per aisle series. */
type ChartRow = { t: number } & Record<string, number>;

export interface Series {
  /** Stable identity — also the row key the chart plots. */
  key: string;
  /** Human label for legend, tooltip and table. */
  label: string;
  color: string;
}

/**
 * Stable identity for a series.
 *
 * Keyed on ids rather than display names so the colour assignment does not move
 * when the camera directory happens to load late, and so two stores that both
 * call an aisle "Aisle 1" stay separate lines.
 */
export function seriesKeyOf(storeId: number | null, aisle: string | null): string {
  return `${storeId ?? '?'}::${aisle ?? '?'}`;
}

export function formatAxisDate(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

export function formatFullDate(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(ms));
}

/**
 * Series label straight from the fields the API joined in — no client-side
 * re-derivation of which camera belongs to which store.
 */
export function reportLabel(report: ShelfReport): string {
  const parts = [report.store_name, report.aisle].filter(Boolean);
  if (parts.length > 0) return parts.join(' · ');
  return report.camera_id ? `Camera ${report.camera_id}` : 'Unassigned';
}

export default function Trends() {
  const [reports, setReports] = useState<ShelfReport[]>([]);
  const [cameras, setCameras] = useState<CameraRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [aisle, setAisle] = useState('');
  const [showTable, setShowTable] = useState(false);

  // The camera directory drives the aisle filter and the colour assignment.
  useEffect(() => {
    const controller = new AbortController();
    listCameras(controller.signal).then(setCameras, () => undefined);
    return () => controller.abort();
  }, []);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        // /reports filters by aisle server-side and is scoped to the signed-in
        // user's store, so the chart only ever receives the rows it is about to plot.
        const page = await listReports(
          {
            limit: REPORTS_LIMIT,
            aisle: aisle || undefined,
          },
          signal,
        );
        setReports(page.items);
      } catch (err) {
        if (isAbortError(err)) return;
        setError(describeError(err));
        setReports([]);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [aisle],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  /** Aisles offered by the signed-in store's cameras. */
  const aisleOptions = useMemo(
    () => [...new Set(cameras.map((c) => c.aisle))].sort(),
    [cameras],
  );

  /**
   * Colour per series, assigned from the *camera directory* rather than from
   * whatever the current result set happens to contain.
   *
   * This is the point: narrowing to one aisle, or a day with no captures, must
   * not repaint the lines that remain. A reader who learned "Aisle 7 is blue"
   * keeps that. (The directory is already limited to the signed-in user's store
   * by the API, so there is no store filter to apply here.)
   */
  const colorByKey = useMemo(() => {
    const ordered = [
      ...new Set(cameras.map((c) => seriesKeyOf(c.store_id, c.aisle))),
    ].sort();

    const map = new Map<string, string>();
    ordered.forEach((key, i) => {
      if (i < SERIES_COLORS.length) map.set(key, SERIES_COLORS[i]);
    });
    return map;
  }, [cameras]);

  /**
   * Pivot reports into one row per timestamp with a column per aisle, oldest
   * first. Reports arrive newest-first and each row only carries its own aisle's
   * value, so the lines are drawn with connectNulls to bridge the gaps.
   *
   * Series past the eighth slot are listed by name instead of being drawn: a
   * generated ninth hue would be indistinguishable from an existing one under
   * colour-vision deficiency. Folding them into an "Other" line was the other
   * option and is wrong here — averaging fill across unrelated aisles is not a
   * quantity anyone measured.
   */
  const { data, plotted, hidden } = useMemo(() => {
    const labels = new Map<string, string>();
    const byTime = new Map<number, ChartRow>();
    const present: string[] = [];
    // Slots the current data actually needs, for series with no directory entry
    // (a camera deleted after its reports were written).
    const spare = SERIES_COLORS.filter(
      (c) => ![...colorByKey.values()].includes(c),
    );
    const fallback = new Map<string, string>();

    for (const report of reports) {
      const ms = new Date(report.created_at).getTime();
      if (!Number.isFinite(ms)) continue;

      const key = seriesKeyOf(report.store_id, report.aisle);
      if (!labels.has(key)) {
        labels.set(key, reportLabel(report));
        present.push(key);
      }

      // A low-confidence capture (the detector found nothing to measure — its numeric
      // fill is a vacuous 1.0) has no value to plot, so leave this point empty and let
      // the line bridge the gap rather than drawing a fake 100%.
      if (report.low_confidence) continue;

      const row = byTime.get(ms) ?? ({ t: ms } as ChartRow);
      row[key] = Math.round(report.fill_percentage * 1000) / 10;
      byTime.set(ms, row);
    }

    present.sort((a, b) => (labels.get(a) ?? '').localeCompare(labels.get(b) ?? ''));
    for (const key of present) {
      if (colorByKey.has(key)) continue;
      const next = spare[fallback.size];
      if (next) fallback.set(key, next);
    }

    const drawn: Series[] = [];
    const skipped: string[] = [];
    for (const key of present) {
      const color = colorByKey.get(key) ?? fallback.get(key);
      const label = labels.get(key) ?? key;
      if (color) drawn.push({ key, label, color });
      else skipped.push(label);
    }

    return {
      data: [...byTime.values()].sort((a, b) => a.t - b.t),
      plotted: drawn,
      hidden: skipped,
    };
  }, [reports, colorByKey]);

  /**
   * Direct end-labels, but only when they will actually read: at most four
   * series, and no two finishing within `END_LABEL_MIN_GAP` of each other.
   * Converging lines get the legend and tooltip instead — nudging collided
   * labels apart detaches them from their lines and reads as noise.
   */
  const endLabels = useMemo(() => {
    if (plotted.length < 2 || plotted.length > 4 || data.length === 0) return null;

    const finals = plotted.map((s) => {
      for (let i = data.length - 1; i >= 0; i -= 1) {
        const value = data[i][s.key];
        if (typeof value === 'number') return { key: s.key, index: i, value };
      }
      return null;
    });
    if (finals.some((f) => f === null)) return null;

    const resolved = finals as { key: string; index: number; value: number }[];
    const sorted = [...resolved].sort((a, b) => a.value - b.value);
    for (let i = 1; i < sorted.length; i += 1) {
      if (sorted[i].value - sorted[i - 1].value < END_LABEL_MIN_GAP) return null;
    }
    return new Map(resolved.map((f) => [f.key, f.index]));
  }, [plotted, data]);

  const showDots = data.length <= DOT_DENSITY_LIMIT;
  const refetching = loading && data.length > 0;

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Fill Trends</h1>
          <p className="text-xs text-gray-500">
            {loading && data.length === 0
              ? 'Loading reports…'
              : `${data.length} capture${data.length === 1 ? '' : 's'} across ${plotted.length} aisle${
                  plotted.length === 1 ? '' : 's'
                }`}
          </p>
        </div>
        <div className="flex items-start gap-2">
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            aria-pressed={showTable}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50"
          >
            <Table2 className="h-3.5 w-3.5" />
            {showTable ? 'Hide table' : 'Table view'}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-3">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">Aisle</span>
          <select
            value={aisle}
            onChange={(e) => setAisle(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          >
            <option value="">All aisles</option>
            {aisleOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>

        {aisle && (
          <button
            type="button"
            onClick={() => {
              setAisle('');
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
            Could not load reports: <span className="break-words font-semibold">{error}</span>
          </span>
        </div>
      )}

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        {loading && data.length === 0 ? (
          <div
            className="flex items-center justify-center gap-2 text-sm text-gray-600"
            style={{ height: CHART_HEIGHT }}
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading trend data…
          </div>
        ) : data.length === 0 ? (
          <div
            className="flex flex-col items-center justify-center gap-2 text-center"
            style={{ height: CHART_HEIGHT }}
          >
            <ChartIcon className="h-6 w-6 text-gray-400" />
            <p className="text-sm font-semibold text-gray-900">Nothing to plot yet</p>
            <p className="text-xs text-gray-500">
              {aisle
                ? 'No reports match the selected aisle.'
                : 'Upload a shelf capture to start building fill history.'}
            </p>
          </div>
        ) : (
          // Hold the previous render at reduced opacity while refetching rather
          // than flashing a skeleton and jumping the layout.
          <div className={refetching ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
              <LineChart
                data={data}
                margin={{ top: 8, right: endLabels ? 108 : 16, bottom: 8, left: -8 }}
              >
                <CartesianGrid stroke={GRIDLINE} strokeWidth={1} vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={['dataMin', 'dataMax']}
                  tickFormatter={(value) => formatAxisDate(Number(value))}
                  tick={{ fontSize: 11, fill: INK_MUTED }}
                  stroke={AXIS}
                />
                <YAxis
                  domain={[0, 100]}
                  tickFormatter={(value) => `${value}%`}
                  tick={{ fontSize: 11, fill: INK_MUTED }}
                  stroke={AXIS}
                />
                <Tooltip
                  labelFormatter={(label) => formatFullDate(Number(label))}
                  formatter={(value) => `${value}%`}
                  cursor={{ stroke: AXIS, strokeWidth: 1 }}
                  contentStyle={{
                    fontSize: 12,
                    borderRadius: 8,
                    border: `1px solid ${GRIDLINE}`,
                    background: SURFACE,
                  }}
                  // Identity comes from the swatch beside the text, never from
                  // colouring the text itself.
                  itemStyle={{ color: INK_PRIMARY }}
                  labelStyle={{ color: INK_SECONDARY }}
                />
                {/* A single series needs no legend — the heading already names it. */}
                {plotted.length >= 2 && (
                  <Legend
                    wrapperStyle={{ fontSize: 12 }}
                    formatter={(value) => (
                      <span style={{ color: INK_SECONDARY }}>{value}</span>
                    )}
                  />
                )}
                {plotted.map((s) => (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    dot={
                      showDots
                        ? { r: 4, fill: s.color, stroke: SURFACE, strokeWidth: 2 }
                        : false
                    }
                    activeDot={{ r: 5, fill: s.color, stroke: SURFACE, strokeWidth: 2 }}
                    connectNulls
                    isAnimationActive={false}
                  >
                    {endLabels && (
                      <LabelList
                        dataKey={s.key}
                        content={(props) => {
                          const { x, y, index } = props as {
                            x?: number;
                            y?: number;
                            index?: number;
                          };
                          if (index !== endLabels.get(s.key)) return null;
                          if (typeof x !== 'number' || typeof y !== 'number') return null;
                          return (
                            <g>
                              <circle cx={x + 10} cy={y} r={3} fill={s.color} />
                              <text
                                x={x + 18}
                                y={y}
                                dominantBaseline="middle"
                                fontSize={11}
                                fill={INK_SECONDARY}
                              >
                                {s.label}
                              </text>
                            </g>
                          );
                        }}
                      />
                    )}
                  </Line>
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {hidden.length > 0 && (
        <p className="text-xs text-gray-500">
          Not plotted: <span className="font-semibold">{hidden.join(', ')}</span>. The chart
          carries eight aisles at most — filter by aisle to see the rest.
        </p>
      )}

      {showTable && data.length > 0 && (
        <div className="max-h-96 overflow-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full text-xs">
            <caption className="sr-only">
              Fill percentage per capture, by store and aisle.
            </caption>
            <thead className="sticky top-0 border-b border-gray-200 bg-gray-50 text-gray-500">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-semibold">
                  Time
                </th>
                {plotted.map((s) => (
                  <th
                    key={s.key}
                    scope="col"
                    className="whitespace-nowrap px-3 py-2 text-right font-semibold"
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        aria-hidden="true"
                        className="inline-block h-2 w-2 shrink-0 rounded-full"
                        style={{ background: s.color }}
                      />
                      {s.label}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.map((row) => (
                <tr key={row.t}>
                  <th
                    scope="row"
                    className="whitespace-nowrap px-3 py-1.5 text-left font-medium text-gray-700"
                  >
                    {formatFullDate(row.t)}
                  </th>
                  {plotted.map((s) => (
                    <td
                      key={s.key}
                      className="px-3 py-1.5 text-right text-gray-700 [font-variant-numeric:tabular-nums]"
                    >
                      {typeof row[s.key] === 'number' ? `${row[s.key]}%` : '—'}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-gray-400">
        Fill percentage per capture, newest {REPORTS_LIMIT} reports. Higher is better — a falling
        line means stock is depleting.
      </p>
    </section>
  );
}
