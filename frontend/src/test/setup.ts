import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Automatically clean up React DOM trees after each test
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Mock ResizeObserver for Recharts ResponsiveContainer
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
window.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;

// jsdom's HTMLMediaElement.play() is not implemented; the live camera preview
// calls it (guarded) once a MediaStream is attached.
if (typeof HTMLMediaElement !== 'undefined') {
  HTMLMediaElement.prototype.play = vi
    .fn()
    .mockResolvedValue(undefined) as unknown as typeof HTMLMediaElement.prototype.play;
}

// jsdom implements neither URL.createObjectURL nor URL.revokeObjectURL, but the
// UI uses them to preview a captured photo and to trigger CSV downloads.
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = vi.fn(
    (_value: Blob | MediaSource) => `blob:jsdom/${Math.random().toString(36).slice(2)}`,
  ) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
}

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock HTMLCanvasElement getContext for DetectionOverlay
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue({
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    strokeRect: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn().mockReturnValue({ width: 50 }),
    font: '',
    textBaseline: '',
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
  }) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}
