import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UploadCaptureModal } from '../Dashboard';
import * as client from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    uploadCapture: vi.fn(),
    waitForCaptureReport: vi.fn(),
  };
});

describe('UploadCaptureModal - Polling State Machine', () => {
  const mockCameras: client.Camera[] = [
    { id: 1, store_id: 10, aisle: 'Aisle 1', name: 'cam-01' },
    { id: 2, store_id: 10, aisle: 'Aisle 2', name: 'cam-02' },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('handles processing -> completed state transition and triggers onCompleted', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCompleted = vi.fn();

    vi.mocked(client.uploadCapture).mockResolvedValueOnce({
      capture_id: 42,
      status: 'processing',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      report_url: '/api/v1/captures/42/report',
    });

    vi.mocked(client.waitForCaptureReport).mockResolvedValueOnce({
      capture_id: 42,
      status: 'completed',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      fill_percentage: 0.85,
      low_confidence: false,
      empty_region_count: 1,
      previous_fill: null,
      report: { summary: 'Good', urgency: 'low', recommended_action: 'None' },
      detections: [],
      error_message: null,
    });

    render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={onClose}
        onCompleted={onCompleted}
      />
    );

    // Camera select has default
    expect(screen.getByRole('combobox')).toHaveValue('1');

    // Attach file
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    // Click submit
    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    expect(submitBtn).toBeEnabled();
    await user.click(submitBtn);

    // Checks upload & polling
    expect(client.uploadCapture).toHaveBeenCalledTimes(1);
    expect(client.uploadCapture).toHaveBeenCalledWith(
      expect.objectContaining({ camera_id: 1, file }),
      expect.any(AbortSignal)
    );

    await waitFor(() => {
      expect(client.waitForCaptureReport).toHaveBeenCalledWith(
        42,
        expect.objectContaining({ intervalMs: 1500, timeoutMs: 120000 })
      );
    });

    await waitFor(() => {
      expect(onCompleted).toHaveBeenCalledTimes(1);
    });
  });

  it('handles processing -> failed state transition and surfaces error_message', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCompleted = vi.fn();

    vi.mocked(client.uploadCapture).mockResolvedValueOnce({
      capture_id: 99,
      status: 'processing',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      report_url: '/api/v1/captures/99/report',
    });

    vi.mocked(client.waitForCaptureReport).mockResolvedValueOnce({
      capture_id: 99,
      status: 'failed',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      fill_percentage: null,
      low_confidence: false,
      empty_region_count: null,
      previous_fill: null,
      report: null,
      detections: null,
      error_message: 'YOLO model failed to load weights from disk',
    });

    render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={onClose}
        onCompleted={onCompleted}
      />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    await user.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/yolo model failed to load weights from disk/i)).toBeInTheDocument();
    });

    expect(onCompleted).not.toHaveBeenCalled();
  });

  it('handles 120s timeout and displays descriptive error', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onCompleted = vi.fn();

    vi.mocked(client.uploadCapture).mockResolvedValueOnce({
      capture_id: 101,
      status: 'processing',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      report_url: '/api/v1/captures/101/report',
    });

    vi.mocked(client.waitForCaptureReport).mockRejectedValueOnce(
      new Error('Capture 101 still processing after 120000ms.')
    );

    render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={onClose}
        onCompleted={onCompleted}
      />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    await user.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/capture 101 still processing after 120000ms/i)).toBeInTheDocument();
    });

    expect(onCompleted).not.toHaveBeenCalled();
  });

  it('aborts active request when modal unmounts', async () => {
    const user = userEvent.setup();
    let capturedSignal: AbortSignal | undefined;

    vi.mocked(client.uploadCapture).mockImplementationOnce((_, signal) => {
      capturedSignal = signal;
      return new Promise(() => {}); // never resolves
    });

    const { unmount } = render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={vi.fn()}
      />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    await user.click(submitBtn);

    expect(capturedSignal).toBeDefined();
    expect(capturedSignal?.aborted).toBe(false);

    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it('aborts active request when Cancel button is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    let capturedSignal: AbortSignal | undefined;

    vi.mocked(client.uploadCapture).mockImplementationOnce((_, signal) => {
      capturedSignal = signal;
      return new Promise(() => {});
    });

    render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={onClose}
        onCompleted={vi.fn()}
      />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    await user.click(submitBtn);

    const cancelBtn = screen.getByRole('button', { name: /cancel/i });
    await user.click(cancelBtn);

    expect(capturedSignal?.aborted).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('ignores a rapid second click so only one POST /captures/upload fires', async () => {
    const user = userEvent.setup();
    const onCompleted = vi.fn();

    vi.mocked(client.uploadCapture).mockResolvedValueOnce({
      capture_id: 7,
      status: 'processing',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      report_url: '/api/v1/captures/7/report',
    });

    vi.mocked(client.waitForCaptureReport).mockResolvedValueOnce({
      capture_id: 7,
      status: 'completed',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      fill_percentage: 0.8,
      low_confidence: false,
      empty_region_count: 0,
      previous_fill: null,
      report: { summary: 'OK', urgency: 'low', recommended_action: 'None' },
      detections: [],
      error_message: null,
    });

    render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={onCompleted}
      />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);

    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });

    // Simulate a rapid double-click: fire two clicks in the same microtask batch
    // so the second one sees the same stale render before React re-renders.
    submitBtn.click();
    submitBtn.click();

    // Only one upload request should have been made.
    expect(client.uploadCapture).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(onCompleted).toHaveBeenCalledTimes(1);
    });
  });
});

describe('UploadCaptureModal - Live camera capture', () => {
  const mockCameras: client.Camera[] = [
    { id: 1, store_id: 10, aisle: 'Aisle 1', name: 'cam-01' },
  ];

  const originalGetContext = HTMLCanvasElement.prototype.getContext;
  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
  const drawImage = vi.fn();

  /** jsdom has no camera, so navigator.mediaDevices is installed per test. */
  function installMediaDevices(
    getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>,
  ) {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: getUserMedia ? { getUserMedia } : undefined,
    });
  }

  /** Stream double shaped like the real thing, recording track.stop() calls. */
  function fakeStream(width = 1280, height = 720) {
    const stop = vi.fn();
    const track = { stop, getSettings: () => ({ width, height }) };
    const stream = {
      getTracks: () => [track],
      getVideoTracks: () => [track],
    } as unknown as MediaStream;
    return { stream, stop };
  }

  /** jsdom's canvas cannot encode pixels, so toBlob hands back a real Blob. */
  function stubCanvas() {
    HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue({
      drawImage,
    }) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.toBlob = function (callback, type) {
      callback(new Blob(['jpeg-bytes'], { type: type ?? 'image/jpeg' }));
    } as typeof HTMLCanvasElement.prototype.toBlob;
  }

  function mockCompletedPipeline(captureId = 42) {
    vi.mocked(client.uploadCapture).mockResolvedValueOnce({
      capture_id: captureId,
      status: 'processing',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      report_url: `/api/v1/captures/${captureId}/report`,
    });
    vi.mocked(client.waitForCaptureReport).mockResolvedValueOnce({
      capture_id: captureId,
      status: 'completed',
      image_url: '/uploads/test.jpg',
      captured_at: new Date().toISOString(),
      fill_percentage: 0.85,
      low_confidence: false,
      empty_region_count: 1,
      previous_fill: null,
      report: { summary: 'Good', urgency: 'low', recommended_action: 'None' },
      detections: [],
      error_message: null,
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    drawImage.mockClear();
  });

  afterEach(() => {
    HTMLCanvasElement.prototype.getContext = originalGetContext;
    HTMLCanvasElement.prototype.toBlob = originalToBlob;
    if (originalMediaDevices) {
      Object.defineProperty(navigator, 'mediaDevices', originalMediaDevices);
    } else {
      delete (navigator as { mediaDevices?: unknown }).mediaDevices;
    }
  });

  it('captures a live camera frame into a File passed to the unchanged upload flow', async () => {
    const user = userEvent.setup();
    const onCompleted = vi.fn();
    const { stream, stop } = fakeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);

    installMediaDevices(getUserMedia);
    stubCanvas();
    mockCompletedPipeline();

    const { rerender } = render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={onCompleted}
      />
    );

    rerender(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={onCompleted}
      />
    );

    // The modal used to accept an unused `stores` prop. Drop it so older test
    // snapshots keep compiling after the prop was removed from UploadCaptureModal.

    await user.click(screen.getByRole('button', { name: /take photo/i }));

    // The rear camera is preferred on phones; laptops ignore the hint.
    expect(getUserMedia).toHaveBeenCalledWith({
      video: { facingMode: 'environment' },
      audio: false,
    });

    const video = document.querySelector('video') as HTMLVideoElement;
    expect(video).toBeInTheDocument();
    Object.defineProperty(video, 'videoWidth', { value: 1280, configurable: true });
    Object.defineProperty(video, 'videoHeight', { value: 720, configurable: true });

    const captureBtn = screen.getByRole('button', { name: /capture photo/i });
    await waitFor(() => expect(captureBtn).toBeEnabled());
    await user.click(captureBtn);

    // The frame is snapshotted at its intrinsic size and the camera is released.
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1280, 720);
    await waitFor(() =>
      expect(screen.getByAltText(/captured shelf photo/i)).toBeInTheDocument(),
    );
    expect(stop).toHaveBeenCalled();

    // Submit through the existing flow — only the File's origin changed.
    const submitBtn = screen.getByRole('button', { name: /upload & analyze/i });
    expect(submitBtn).toBeEnabled();
    await user.click(submitBtn);

    expect(client.uploadCapture).toHaveBeenCalledTimes(1);
    const [payload] = vi.mocked(client.uploadCapture).mock.calls[0];
    expect(payload.camera_id).toBe(1);
    expect(payload.file).toBeInstanceOf(File);
    expect(payload.file.type).toBe('image/jpeg');
    expect(payload.file.name).toMatch(/^capture-\d+\.jpg$/);
    expect(payload.file.size).toBeGreaterThan(0);

    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
  });

  it('retake discards the captured frame and reopens the camera', async () => {
    const user = userEvent.setup();
    const { stream } = fakeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);

    installMediaDevices(getUserMedia);
    stubCanvas();

    const { rerender } = render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={vi.fn()}
      />
    );

    rerender(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: /take photo/i }));
    const video = document.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(video, 'videoWidth', { value: 640, configurable: true });
    Object.defineProperty(video, 'videoHeight', { value: 480, configurable: true });

    const captureBtn = screen.getByRole('button', { name: /capture photo/i });
    await waitFor(() => expect(captureBtn).toBeEnabled());
    await user.click(captureBtn);

    await waitFor(() => screen.getByRole('button', { name: /retake/i }));
    // The captured frame is a real pending file, so submit is armed.
    expect(screen.getByRole('button', { name: /upload & analyze/i })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: /retake/i }));

    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /upload & analyze/i })).toBeDisabled(),
    );
  });

  it('falls back to the file picker when camera permission is denied', async () => {
    const user = userEvent.setup();
    const onCompleted = vi.fn();
    const getUserMedia = vi
      .fn()
      .mockRejectedValue(
        Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' }),
      );

    installMediaDevices(getUserMedia);
    mockCompletedPipeline();

    const { rerender } = render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={onCompleted}
      />
    );

    rerender(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={onCompleted}
      />
    );

    await user.click(screen.getByRole('button', { name: /take photo/i }));

    await waitFor(() => expect(screen.getByText(/camera access was denied/i)).toBeInTheDocument());

    // The picker is back and the camera controls are gone — the modal still works.
    expect(document.querySelector('input[type="file"]')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /capture photo/i })).toBeNull();

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);
    await user.click(screen.getByRole('button', { name: /upload & analyze/i }));

    expect(client.uploadCapture).toHaveBeenCalledWith(
      expect.objectContaining({ camera_id: 1, file }),
      expect.any(AbortSignal),
    );
    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
  });

  it('surfaces a clear, visible error when the upload fails at the network level', async () => {
    const user = userEvent.setup();
    const onCompleted = vi.fn();

    // What a CORS/offline/connection failure looks like: fetch rejects with a
    // TypeError instead of giving us an HTTP status.
    vi.mocked(client.uploadCapture).mockRejectedValueOnce(new TypeError('Failed to fetch'));

    render(
      <UploadCaptureModal cameras={mockCameras} onClose={vi.fn()} onCompleted={onCompleted} />
    );

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['fakeimage'], 'shelf.jpg', { type: 'image/jpeg' });
    await user.upload(fileInput, file);
    await user.click(screen.getByRole('button', { name: /upload & analyze/i }));

    // The failure must be unmistakable — never a silent no-op.
    const banner = await screen.findByRole('alert');
    expect(within(banner).getByText(/upload failed/i)).toBeInTheDocument();
    expect(within(banner).getByText(/server could not be reached/i)).toBeInTheDocument();
    // The raw browser wording is not shown to the user.
    expect(within(banner).queryByText(/^failed to fetch$/i)).toBeNull();
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it('falls back to the file picker when getUserMedia is unavailable', async () => {
    const user = userEvent.setup();

    installMediaDevices(undefined);

    const { rerender } = render(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={vi.fn()}
      />
    );

    rerender(
      <UploadCaptureModal
        cameras={mockCameras}
        onClose={vi.fn()}
        onCompleted={vi.fn()}
      />
    );

    await user.click(screen.getByRole('button', { name: /take photo/i }));

    await waitFor(() =>
      expect(screen.getByText(/live camera capture is not available/i)).toBeInTheDocument(),
    );
    expect(document.querySelector('input[type="file"]')).not.toBeNull();
  });
});
