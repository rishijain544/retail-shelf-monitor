import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  Camera,
  ChevronRight,
  Download,
  Loader2,
  RefreshCw,
  Upload,
  X,
} from 'lucide-react';

import { describeError, isAbortError } from '../api/errors';
import {
  exportReportsCsv,
  listCameras,
  listReports,
  uploadCapture,
  waitForCaptureReport,
  type Camera as CameraRecord,
  type ShelfReport,
} from '../api/client';

const URGENCY_OPTIONS = ['low', 'medium', 'high'] as const;

/** How often the background analysis pipeline is re-polled after an upload. */
const POLL_INTERVAL_MS = 1500;

/** How long to keep polling one capture before giving up. */
const POLL_TIMEOUT_MS = 120000;

/** Reports fetched per dashboard load (the backend caps `limit` at 200). */
const REPORTS_LIMIT = 60;

const URGENCY_STYLES: Record<string, string> = {
  low: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  high: 'bg-red-50 text-red-700 border-red-200',
};

/** `urgency` is a plain string column, so unknown values get a neutral badge. */
function urgencyClass(urgency: string): string {
  return URGENCY_STYLES[urgency?.toLowerCase()] ?? 'bg-gray-50 text-gray-600 border-gray-200';
}

function fillBarClass(fraction: number): string {
  if (fraction >= 0.6) return 'bg-green-500';
  if (fraction >= 0.3) return 'bg-amber-500';
  return 'bg-red-500';
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

/**
 * Card heading: "Downtown · Aisle 4". Store and aisle are resolved by the API
 * through capture -> camera -> store; fall back to the capture id rather than
 * inventing a location when those rows are gone.
 */
function locationLabel(report: ShelfReport): string {
  const parts = [report.store_name, report.aisle].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : `Capture #${report.capture_id}`;
}

/* ------------------------------------------------------------------ *
 * Report card
 * ------------------------------------------------------------------ */

export function ReportCard({ report, onOpen }: { report: ShelfReport; onOpen: () => void }) {
  // A low-confidence report's numeric fill is the detector's vacuous 1.0 ("nothing
  // found"), NOT a measurement — it must never read as a normal "100% full, Low urgency"
  // card. Show a distinct amber "unable to assess" badge and no fill number instead.
  const unassessed = report.low_confidence;
  const percent = unassessed ? null : Math.round(report.fill_percentage * 100);
  const clamped = percent === null ? 0 : Math.min(100, Math.max(0, percent));
  const barClass = percent === null ? 'bg-gray-300' : fillBarClass(report.fill_percentage);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 text-left shadow-sm transition hover:border-sky-300 hover:shadow focus:outline-none focus:ring-2 focus:ring-sky-500 focus:ring-offset-1"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p
            className="text-sm font-semibold text-gray-900"
            title={locationLabel(report)}
          >
            {locationLabel(report)}
          </p>
          <p className="text-xs text-gray-500" title={report.camera_name ?? undefined}>
            {report.camera_name ?? `Camera ${report.camera_id ?? '—'}`}
          </p>
        </div>
        {unassessed ? (
          <span
            title="Unable to assess — no confident detections"
            className="shrink-0 rounded-full border bg-amber-100 border-amber-300 px-2 py-0.5 text-xs font-semibold text-amber-800"
          >
            unable to assess
          </span>
        ) : (
          <span
            className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${urgencyClass(
              report.urgency,
            )}`}
          >
            {report.urgency}
          </span>
        )}
      </div>

      <div>
        <div className="flex items-baseline gap-1.5">
          <span className="text-2xl font-semibold text-gray-900">
            {percent === null ? '—' : `${percent}%`}
          </span>
          <span className="text-xs text-gray-500">
            {percent === null ? 'not assessed' : 'full'}
          </span>
        </div>
        <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
          <div className={`h-full rounded-full ${barClass}`} style={{ width: `${clamped}%` }} />
        </div>
      </div>

      {report.recommended_action ? (
        <p className="line-clamp-2 text-xs leading-relaxed text-gray-600">
          {report.recommended_action}
        </p>
      ) : (
        <p className="text-xs italic text-gray-400">No recommended action recorded.</p>
      )}

      <div className="mt-auto flex items-center justify-between border-t border-gray-100 pt-2 text-xs text-gray-500">
        <span>{formatTimestamp(report.created_at)}</span>
        <span className="inline-flex items-center gap-0.5 font-semibold text-sky-600 group-hover:text-sky-700">
          Details
          <ChevronRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </button>
  );
}

/* ------------------------------------------------------------------ *
 * Upload capture modal
 * ------------------------------------------------------------------ */

type UploadPhase =
  | { kind: 'idle' }
  | { kind: 'uploading' }
  | { kind: 'processing'; captureId: number }
  | { kind: 'error'; message: string };

export interface UploadModalProps {
  cameras: CameraRecord[];
  onClose: () => void;
  onCompleted: () => void;
}

/** How the modal is currently obtaining the shelf image. */
type CaptureSource = 'file' | 'camera';

const CAPTURE_MIME = 'image/jpeg';
const CAPTURE_QUALITY = 0.92;

/** Promisified `canvas.toBlob` — not implemented in every browser/jsdom. */
function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    if (typeof canvas.toBlob !== 'function') {
      reject(new Error('This browser cannot export the captured frame.'));
      return;
    }
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else reject(new Error('The captured frame could not be encoded.'));
      },
      type,
      quality,
    );
  });
}

/**
 * Snapshot size: the video element's intrinsic frame is authoritative, but the
 * track's own settings are already known before the first frame is decoded.
 */
function frameSize(
  video: HTMLVideoElement,
  stream: MediaStream | null,
): { width: number; height: number } {
  if (video.videoWidth > 0 && video.videoHeight > 0) {
    return { width: video.videoWidth, height: video.videoHeight };
  }
  const settings = stream?.getVideoTracks()[0]?.getSettings();
  return { width: settings?.width ?? 0, height: settings?.height ?? 0 };
}

/** Plain-language reason the live preview could not start. */
function cameraErrorMessage(error: unknown): string {
  const name = (error as { name?: string } | null)?.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Camera access was denied, so you can upload a file instead.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'No usable camera was found on this device, so you can upload a file instead.';
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return 'The camera is already in use by another app, so you can upload a file instead.';
  }
  return 'The camera could not be started, so you can upload a file instead.';
}

export function UploadCaptureModal({ cameras, onClose, onCompleted }: UploadModalProps) {
  const [cameraId, setCameraId] = useState(() => (cameras[0] ? String(cameras[0].id) : ''));
  const [file, setFile] = useState<File | null>(null);
  // Which input produced the pending `file`, so switching input modes drops a
  // stale pick instead of silently uploading the wrong image.
  const [fileSource, setFileSource] = useState<CaptureSource | null>(null);
  const [phase, setPhase] = useState<UploadPhase>({ kind: 'idle' });

  // Live-camera state; `previewUrl` doubles as "a frame has been captured".
  const [source, setSource] = useState<CaptureSource>('file');
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  // Synchronous guard against double-submit — set immediately on click, before
  // any async work, so a second click in the same tick cannot pass the guard.
  const submittingRef = useRef(false);
  const cooldownUntilRef = useRef(0);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const releasePreview = useCallback(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    setPreviewUrl(null);
  }, []);

  // Abort an in-flight upload/poll and release the camera if the modal unmounts.
  useEffect(
    () => () => {
      abortRef.current?.abort();
      stopStream();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    [stopStream],
  );

  // Open the live preview whenever camera mode is selected without a captured
  // frame yet. Any failure degrades to the file picker rather than blocking the
  // modal; the upload/poll flow after capture is completely unchanged.
  useEffect(() => {
    if (source !== 'camera' || previewUrl) return;

    const mediaDevices = typeof navigator === 'undefined' ? undefined : navigator.mediaDevices;
    if (!mediaDevices?.getUserMedia) {
      setCameraError(
        'Live camera capture is not available in this browser, so upload a file instead.',
      );
      setSource('file');
      return;
    }

    let cancelled = false;

    // Rear camera first on phones; laptops without one ignore the hint and hand
    // back their only camera.
    mediaDevices
      .getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        const video = videoRef.current;
        if (video) {
          video.srcObject = stream;
          // autoplay normally starts playback; some browsers need a nudge.
          try {
            const played = video.play?.();
            if (played && typeof played.catch === 'function') played.catch(() => undefined);
          } catch {
            /* autoplay policy — the preview may stay blank, capture still works */
          }
        }
        setCameraReady(true);
        setCameraError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        stopStream();
        setCameraReady(false);
        // Graceful fallback: reopen the file picker and explain why.
        setCameraError(cameraErrorMessage(error));
        setSource('file');
      });

    return () => {
      cancelled = true;
      setCameraReady(false);
      stopStream();
    };
  }, [source, previewUrl, stopStream]);

  const busy = phase.kind === 'uploading' || phase.kind === 'processing';
  const canSubmit =
    Boolean(file) &&
    cameraId !== '' &&
    !busy &&
    !submittingRef.current &&
    Date.now() >= cooldownUntilRef.current;

  // Post-success cooldown (ms) — prevents a near-duplicate re-capture by
  // accident when the user immediately reopens the modal and clicks again.
  const POST_SUCCESS_COOLDOWN_MS = 3000;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file || cameraId === '' || busy || submittingRef.current) return;

    // Lock immediately — synchronous, so a second click in the same tick
    // cannot pass the submittingRef check above.
    submittingRef.current = true;
    setPhase({ kind: 'uploading' });

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // 1. POST /captures/upload -> 202 receipt, analysis runs in the background.
      // The camera_id is the only scope we send; the backend derives the store
      // from the currently authenticated user and ignores any client store claim.
      const receipt = await uploadCapture(
        { camera_id: Number(cameraId), file },
        controller.signal,
      );
      setPhase({ kind: 'processing', captureId: receipt.capture_id });

      // 2. Poll GET /captures/{id}/report until it leaves "processing".
      const result = await waitForCaptureReport(receipt.capture_id, {
        intervalMs: POLL_INTERVAL_MS,
        timeoutMs: POLL_TIMEOUT_MS,
        signal: controller.signal,
      });

      // 3/4. Terminal status: surface the pipeline's own error, or refresh.
      if (result.status === 'failed') {
        setPhase({
          kind: 'error',
          message: result.error_message ?? 'Analysis failed, but the server reported no reason.',
        });
        return;
      }
      onCompleted();
      // Lock the modal out for a short cooldown so a near-duplicate re-capture
      // cannot be fired by immediately reopening and clicking again.
      cooldownUntilRef.current = Date.now() + POST_SUCCESS_COOLDOWN_MS;
    } catch (err) {
      if (isAbortError(err)) return;
      // Keep the raw cause in the console for debugging; the user-facing banner
      // below always renders something actionable.
      console.error('Capture upload failed:', err);
      setPhase({ kind: 'error', message: describeError(err) });
    } finally {
      // Release the synchronous guard — the disabled state is still governed by
      // `phase` (uploading/processing/error), so the button stays disabled while
      // work is in flight regardless of this ref.
      submittingRef.current = false;
    }
  }

  /** Switch between the file picker and the live camera preview. */
  function switchSource(next: CaptureSource) {
    if (busy || next === source) return;
    setCameraError(null);
    // Drop a pick made in the other mode so we never upload the wrong image.
    if (fileSource !== null && fileSource !== next) {
      setFile(null);
      setFileSource(null);
      setPhase({ kind: 'idle' });
      releasePreview();
    }
    setSource(next);
  }

  /**
   * Snapshot the current video frame to a canvas and turn it into a `File`.
   * Everything downstream (upload, poll, refresh) is untouched — this is only a
   * different way of producing the `File` the submit handler already expects.
   */
  async function handleCapture() {
    const video = videoRef.current;
    if (!video) return;

    const { width, height } = frameSize(video, streamRef.current);
    if (!width || !height) {
      setCameraError('The camera is still starting — try again in a moment.');
      return;
    }

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) {
      setCameraError('This browser cannot read the camera frame, so upload a file instead.');
      return;
    }
    context.drawImage(video, 0, 0, width, height);

    try {
      const blob = await canvasToBlob(canvas, CAPTURE_MIME, CAPTURE_QUALITY);
      const captured = new File([blob], `capture-${Date.now()}.jpg`, { type: CAPTURE_MIME });
      setFile(captured);
      setFileSource('camera');
      setPhase({ kind: 'idle' });
      setCameraError(null);

      // Freeze the frame: show it as a still and release the camera.
      releasePreview();
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      setPreviewUrl(url);
      stopStream();
      setCameraReady(false);
    } catch (error) {
      setCameraError(describeError(error));
    }
  }

  /** Discard the captured frame and reopen the live preview. */
  function handleRetake() {
    setFile(null);
    setFileSource(null);
    setPhase({ kind: 'idle' });
    setCameraError(null);
    // Clearing `previewUrl` restarts the camera via the effect above.
    releasePreview();
  }

  function handleClose() {
    abortRef.current?.abort();
    stopStream();
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="upload-capture-title"
    >
      <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <h2 id="upload-capture-title" className="text-sm font-semibold text-gray-900">
            Upload shelf capture
          </h2>
          <button
            type="button"
            onClick={handleClose}
            className="rounded p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 px-4 py-4">              {cameras.length === 0 ? (
            <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              <Camera className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                No cameras are registered yet. A capture must be attached to a camera, so add one
                to the backend before uploading.
              </span>
            </div>
          ) : (
            <label className="block">
              <span className="mb-1 block text-xs font-semibold text-gray-700">Camera</span>
              <select
                value={cameraId}
                onChange={(e) => setCameraId(e.target.value)}
                disabled={busy}
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:bg-gray-50"
              >
                {cameras.map((camera) => (
                  <option key={camera.id} value={camera.id}>
                    {camera.aisle} — {camera.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="block">
            <span className="mb-1 block text-xs font-semibold text-gray-700">Shelf image</span>

            {/* Source picker — a store employee in front of a shelf uses the
                camera; anything else keeps the plain file picker. */}
            <div
              role="group"
              aria-label="Shelf image source"
              className="mb-2 inline-flex rounded-md border border-gray-300 p-0.5"
            >
              {(
                [
                  { value: 'file', label: 'Upload file', Icon: Upload },
                  { value: 'camera', label: 'Take Photo', Icon: Camera },
                ] as const
              ).map(({ value, label, Icon }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => switchSource(value)}
                  disabled={busy}
                  aria-pressed={source === value}
                  className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-semibold transition disabled:cursor-not-allowed ${
                    source === value
                      ? 'bg-sky-600 text-white'
                      : 'text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>

            {source === 'file' ? (
              <input
                type="file"
                accept="image/*"
                disabled={busy}
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setFileSource(e.target.files?.[0] ? 'file' : null);
                  setPhase({ kind: 'idle' });
                }}
                className="w-full text-xs text-gray-600 file:mr-3 file:rounded-md file:border-0 file:bg-sky-50 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-sky-700 hover:file:bg-sky-100 disabled:opacity-60"
              />
            ) : (
              <div className="space-y-2">
                {previewUrl ? (
                  <img
                    src={previewUrl}
                    alt="Captured shelf photo"
                    className="w-full rounded-md border border-gray-200"
                  />
                ) : (
                  <div className="relative overflow-hidden rounded-md border border-gray-800 bg-gray-900">
                    <video
                      ref={videoRef}
                      autoPlay
                      playsInline
                      muted
                      className="h-44 w-full object-cover"
                    />
                    {!cameraReady && (
                      <p className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-gray-200">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        Starting camera…
                      </p>
                    )}
                  </div>
                )}

                {previewUrl ? (
                  <button
                    type="button"
                    onClick={handleRetake}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Retake
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleCapture()}
                    disabled={busy || !cameraReady}
                    className="inline-flex items-center gap-1.5 rounded-md bg-gray-800 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-900 disabled:cursor-not-allowed disabled:bg-gray-300"
                  >
                    <Camera className="h-3.5 w-3.5" />
                    Capture photo
                  </button>
                )}
              </div>
            )}
          </div>

          {cameraError && (
            <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="break-words">{cameraError}</span>
            </p>
          )}

          {phase.kind === 'uploading' && (
            <p className="flex items-center gap-2 text-xs text-gray-600">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Uploading image…
            </p>
          )}

          {phase.kind === 'processing' && (
            <p className="flex items-center gap-2 text-xs text-gray-600">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Analyzing capture #{phase.captureId} — checking every{' '}
              {(POLL_INTERVAL_MS / 1000).toFixed(1)}s…
            </p>
          )}

          {phase.kind === 'error' && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-xs text-red-700"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="space-y-0.5">
                <p className="font-semibold text-red-800">Upload failed</p>
                <p className="break-words">{phase.message}</p>
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 border-t border-gray-100 pt-3">
            <button
              type="button"
              onClick={handleClose}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50"
            >
              {busy ? 'Cancel' : 'Close'}
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {busy ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {phase.kind === 'uploading' ? 'Uploading…' : 'Processing…'}
                </>
              ) : (
                <>
                  <Upload className="h-3.5 w-3.5" />
                  Upload &amp; analyze
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Dashboard
 * ------------------------------------------------------------------ */

export default function Dashboard() {
  const navigate = useNavigate();

  // Access control lives in <AuthGate> (App.tsx), which redirects to /login when
  // there is no valid session; by the time this page renders, a session exists.

  const [reports, setReports] = useState<ShelfReport[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [cameras, setCameras] = useState<CameraRecord[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);

  // Server-side report filters (passed directly to GET /reports). Store is now
  // derived from the logged-in user's store_id, so there is no store dropdown.
  const [aisleFilter, setAisleFilter] = useState('');
  const [urgencyFilter, setUrgencyFilter] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');

  // CSV export state.
  const [exporting, setExporting] = useState(false);

  const filtersActive = Boolean(aisleFilter || urgencyFilter || fromDate || toDate);

  async function handleExportCsv() {
    setExporting(true);
    try {
      const response = await exportReportsCsv(
        {
          aisle: aisleFilter || undefined,
          urgency: (urgencyFilter as import('../api/client').Urgency) || undefined,
          start_date: fromDate || undefined,
          end_date: toDate || undefined,
        },
      );
      if (!response.ok) {
        throw new Error(`Export failed: ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = response.headers.get('content-disposition')?.match(/filename=([^;]+)/)?.[1]?.replace(/"/g, '') ?? 'shelf-reports.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      if (isAbortError(err)) return;
      console.error('CSV export failed:', err);
    } finally {
      setExporting(false);
    }
  }

  const loadReports = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      // GET /reports answers a Page envelope — the rows are in `items`.
      // Scope is derived from the authenticated user's store_id on the backend,
      // so we do not pass a store_id here.
      const page = await listReports(
        {
          limit: REPORTS_LIMIT,
          aisle: aisleFilter || undefined,
          urgency: (urgencyFilter as import('../api/client').Urgency) || undefined,
          start_date: fromDate || undefined,
          end_date: toDate || undefined,
        },
        signal,
      );
      setReports(page.items);
      setTotal(page.total);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(describeError(err));
      setReports([]);
      setTotal(0);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [aisleFilter, urgencyFilter, fromDate, toDate]);

  useEffect(() => {
    const controller = new AbortController();
    void loadReports(controller.signal);

    // The camera directory only feeds the upload picker now.
    listCameras(controller.signal).then(setCameras, () => undefined);

    return () => controller.abort();
  }, [loadReports]);

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Shelf Reports</h1>
          <p className="text-xs text-gray-500">
            {loading
              ? 'Loading reports…'
              : `${reports.length} of ${total} report${total === 1 ? '' : 's'}, newest first`}
          </p>
        </div>

        <div className="flex items-start gap-2">
          <button
            type="button"
            onClick={() => void loadReports()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-700"
          >
            <Upload className="h-3.5 w-3.5" />
            Upload Capture
          </button>
        </div>
      </header>

      {/* Filters — mirror the Alerts/ Trends filter bar; values go straight to
          GET /reports so the backend does the filtering and pagination. */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 bg-white p-3">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">Aisle</span>
          <input
            type="text"
            value={aisleFilter}
            onChange={(e) => setAisleFilter(e.target.value)}
            placeholder="e.g. Aisle 7"
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-700">Urgency</span>
          <select
            value={urgencyFilter}
            onChange={(e) => setUrgencyFilter(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1.5 text-xs capitalize text-gray-900 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500"
          >
            <option value="">All</option>
            {URGENCY_OPTIONS.map((u) => (
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
              setAisleFilter('');
              setUrgencyFilter('');
              setFromDate('');
              setToDate('');
            }}
            className="rounded-md px-2 py-1.5 text-xs font-semibold text-sky-600 transition hover:bg-sky-50"
          >
            Clear filters
          </button>
        )}

        <button
          type="button"
          onClick={handleExportCsv}
          disabled={exporting || !filtersActive}
          className="inline-flex items-center gap-1.5 rounded-md bg-gray-800 px-2 py-1.5 text-xs font-semibold text-white transition hover:bg-gray-900 disabled:cursor-not-allowed disabled:bg-gray-300"
        >
          {exporting ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Exporting…
            </>
          ) : (
            <>
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Could not load reports: <span className="break-words font-semibold">{error}</span>
          </span>
        </div>
      )}

      {loading && reports.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white p-8 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading reports…
        </div>
      )}

      {!loading && !error && reports.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-300 bg-white p-8 text-center">
          <Camera className="mx-auto h-6 w-6 text-gray-400" />
          {filtersActive ? (
            <>
              <p className="mt-2 text-sm font-semibold text-gray-900">No reports match these filters</p>
              <p className="mt-1 text-xs text-gray-500">
                Try widening the aisle, urgency, or date range.
              </p>
            </>
          ) : (
            <>
              <p className="mt-2 text-sm font-semibold text-gray-900">No shelf reports yet</p>
              <p className="mt-1 text-xs text-gray-500">
                Upload a shelf capture to run the detection pipeline and produce the first report.
              </p>
            </>
          )}
        </div>
      )}

      {reports.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {reports.map((report) => (
            <ReportCard
              key={report.id}
              report={report}
              onOpen={() => navigate(`/reports/${report.id}`)}
            />
          ))}
        </div>
      )}

      {uploadOpen && (
        <UploadCaptureModal
          cameras={cameras}
          onClose={() => setUploadOpen(false)}
          onCompleted={() => {
            setUploadOpen(false);
            void loadReports();
          }}
        />
      )}
    </section>
  );
}
