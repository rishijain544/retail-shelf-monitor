import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertCircle, ArrowLeft, ImageOff, Loader2 } from 'lucide-react';

import { describeError, isAbortError } from '../api/errors';
import {
  getReport,
  pollCaptureReport,
  resolveImageUrl,
  type CaptureReport,
  type ShelfReport,
} from '../api/client';

const URGENCY_STYLES: Record<string, string> = {
  low: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  high: 'bg-red-50 text-red-700 border-red-200',
};

function urgencyClass(urgency: string): string {
  return URGENCY_STYLES[urgency?.toLowerCase()] ?? 'bg-gray-50 text-gray-600 border-gray-200';
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

/** A drawable box: [xmin, ymin, xmax, ymax] in source-image pixels. */
export type Box = [number, number, number, number];

export function toBox(raw: unknown): Box | null {
  if (!Array.isArray(raw) || raw.length < 4) return null;
  const [x1, y1, x2, y2] = raw.map(Number);
  if ([x1, y1, x2, y2].some((n) => !Number.isFinite(n))) return null;
  return [x1, y1, x2, y2];
}

/* ------------------------------------------------------------------ *
 * Canvas overlay
 * ------------------------------------------------------------------ */

export interface OverlayProps {
  imageUrl: string;
  boxes: Box[];
  confidences: (number | null)[];
}

/**
 * The capture image with its detected empty regions stroked on a <canvas>
 * layered above it.
 *
 * The canvas backing store is sized to the image's intrinsic pixels
 * (naturalWidth/Height) because bboxes are stored in those coordinates, while
 * CSS stretches both image and canvas to the container width — so the overlay
 * stays registered at any display size without rescaling every box.
 */
export function DetectionOverlay({ imageUrl, boxes, confidences }: OverlayProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  // A new image has to re-report its intrinsic size before we can draw.
  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [imageUrl]);

  const draw = useCallback(() => {
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas || !img.naturalWidth) return;

    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Scale strokes/labels with the image so they stay legible on 4K captures.
    const lineWidth = Math.max(2, canvas.width / 400);
    const fontSize = Math.max(12, canvas.width / 70);
    ctx.font = `600 ${fontSize}px ui-sans-serif, system-ui, sans-serif`;
    ctx.textBaseline = 'middle';

    boxes.forEach(([x1, y1, x2, y2], i) => {
      const w = x2 - x1;
      const h = y2 - y1;

      ctx.fillStyle = 'rgba(220, 38, 38, 0.16)';
      ctx.fillRect(x1, y1, w, h);
      ctx.strokeStyle = 'rgba(220, 38, 38, 0.95)';
      ctx.lineWidth = lineWidth;
      ctx.strokeRect(x1, y1, w, h);

      const confidence = confidences[i];
      const label =
        confidence === null || confidence === undefined
          ? 'empty'
          : `empty ${Math.round(confidence * 100)}%`;

      const padding = fontSize * 0.35;
      const boxW = ctx.measureText(label).width + padding * 2;
      const boxH = fontSize * 1.5;
      // Park the label inside the frame when the box hugs the top edge.
      const labelY = y1 - boxH < 0 ? y1 : y1 - boxH;

      ctx.fillStyle = 'rgba(220, 38, 38, 0.95)';
      ctx.fillRect(x1, labelY, boxW, boxH);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(label, x1 + padding, labelY + boxH / 2);
    });
  }, [boxes, confidences]);

  useEffect(() => {
    if (loaded) draw();
  }, [loaded, draw]);

  if (failed) {
    return (
      <div className="flex h-56 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 bg-gray-50 text-xs text-gray-500">
        <ImageOff className="h-5 w-5 text-gray-400" />
        Capture image could not be loaded.
      </div>
    );
  }

  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-gray-200 bg-gray-900">
      <img
        ref={imgRef}
        src={imageUrl}
        alt="Shelf capture"
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
        className="block w-full"
      />
      <canvas
        ref={canvasRef}
        className="pointer-events-none absolute inset-0 h-full w-full"
        aria-hidden="true"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Page
 * ------------------------------------------------------------------ */

export default function ShelfDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [report, setReport] = useState<ShelfReport | null>(null);
  const [capture, setCapture] = useState<CaptureReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [captureError, setCaptureError] = useState<string | null>(null);

  const reportId = Number(id);

  useEffect(() => {
    if (!Number.isInteger(reportId) || reportId <= 0) {
      setError(`"${id}" is not a valid report id.`);
      setLoading(false);
      return;
    }

    const controller = new AbortController();

    (async () => {
      setLoading(true);
      setError(null);
      setCaptureError(null);
      try {
        const shelfReport = await getReport(reportId, controller.signal);
        setReport(shelfReport);

        // The image and per-detection boxes live on the capture, not the report.
        try {
          setCapture(await pollCaptureReport(shelfReport.capture_id, controller.signal));
        } catch (err) {
          if (!isAbortError(err)) setCaptureError(describeError(err));
        }
      } catch (err) {
        if (isAbortError(err)) return;
        setError(describeError(err));
        setReport(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();

    return () => controller.abort();
  }, [reportId, id]);

  const imageUrl = capture?.image_url ? resolveImageUrl(capture.image_url) : null;

  // Prefer the detection rows (they carry confidence); fall back to the boxes
  // snapshotted on the report itself when detections are absent.
  const { boxes, confidences } = useMemo(() => {
    const empties = (capture?.detections ?? []).filter((d) => d.class === 'empty');
    if (empties.length > 0) {
      const parsed = empties
        .map((d) => ({ box: toBox(d.bbox), confidence: d.confidence }))
        .filter((entry): entry is { box: Box; confidence: number } => entry.box !== null);
      return {
        boxes: parsed.map((p) => p.box),
        confidences: parsed.map((p) => p.confidence as number | null),
      };
    }
    const fallback = (report?.empty_regions_json ?? [])
      .map(toBox)
      .filter((b): b is Box => b !== null);
    return { boxes: fallback, confidences: fallback.map(() => null) };
  }, [capture?.detections, report?.empty_regions_json]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-8 text-sm text-gray-600">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading report…
      </div>
    );
  }

  if (error || !report) {
    return (
      <section className="space-y-4">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:text-sky-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to dashboard
        </button>
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="break-words">{error ?? 'Report not found.'}</span>
        </div>
      </section>
    );
  }

  // A low-confidence capture's numeric fill is the detector's vacuous 1.0 ("nothing
  // found"), NOT a measurement — show "unable to assess" rather than a confident 100%.
  const unassessed = report.low_confidence;
  const percent = unassessed ? null : Math.round(report.fill_percentage * 100);
  const location = [report.store_name, report.aisle].filter(Boolean).join(' · ');

  return (
    <section className="space-y-4">
      <button
        type="button"
        onClick={() => navigate('/')}
        className="inline-flex items-center gap-1 text-xs font-semibold text-sky-600 hover:text-sky-700"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to dashboard
      </button>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">
            {location || `Capture #${report.capture_id}`}
          </h1>
          <p className="text-xs text-gray-500">
            {report.camera_name ? `${report.camera_name} · ` : ''}
            Report #{report.id} · {formatTimestamp(report.created_at)}
          </p>
        </div>
        {unassessed ? (
          <span
            title="Unable to assess — no confident detections"
            className="rounded-full border bg-amber-100 border-amber-300 px-2.5 py-1 text-xs font-semibold text-amber-800"
          >
            Unable to assess
          </span>
        ) : (
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold capitalize ${urgencyClass(
              report.urgency,
            )}`}
          >
            {`${report.urgency} urgency`}
          </span>
        )}
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          {imageUrl ? (
            <DetectionOverlay imageUrl={imageUrl} boxes={boxes} confidences={confidences} />
          ) : (
            <div className="flex h-56 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 bg-gray-50 text-xs text-gray-500">
              <ImageOff className="h-5 w-5 text-gray-400" />
              {captureError ?? 'No image is attached to this capture.'}
            </div>
          )}

          <p className="text-xs text-gray-500">
            {boxes.length > 0
              ? `${boxes.length} empty region${boxes.length === 1 ? '' : 's'} outlined in red.`
              : unassessed
                ? 'No empty regions were detected on this capture — the detector found nothing it could measure, so this means the shelf could not be assessed, not that it is confirmed full.'
                : 'No empty regions were detected on this capture.'}
          </p>

          {captureError && imageUrl && (
            <p className="text-xs text-amber-700">
              Detection data may be incomplete: {captureError}
            </p>
          )}
        </div>

        <aside className="space-y-4">
          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Fill level</p>
            <p className="mt-1 text-3xl font-semibold text-gray-900">
              {percent === null ? '—' : `${percent}%`}
            </p>
            {percent === null && (
              <p className="mt-1 text-xs text-gray-500">Not assessed</p>
            )}
            {capture?.previous_fill !== null && capture?.previous_fill !== undefined && (
              <p className="mt-1 text-xs text-gray-500">
                Previous capture: {Math.round(capture.previous_fill * 100)}%
              </p>
            )}
          </div>

          <div className="rounded-lg border border-gray-200 bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Summary</p>
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-gray-700">
              {report.llm_summary ?? 'No summary was generated for this report.'}
            </p>
          </div>

          <div className="rounded-lg border border-sky-200 bg-sky-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-sky-700">
              Recommended action
            </p>
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-sky-900">
              {report.recommended_action ?? 'No recommended action was recorded.'}
            </p>
          </div>
        </aside>
      </div>
    </section>
  );
}
