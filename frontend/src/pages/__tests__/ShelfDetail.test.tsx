import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ShelfDetail, { DetectionOverlay, toBox } from '../ShelfDetail';
import * as client from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    getReport: vi.fn(),
    pollCaptureReport: vi.fn(),
    resolveImageUrl: (url: string) => `http://localhost:8000${url}`,
  };
});

describe('ShelfDetail Page & DetectionOverlay', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('toBox helper', () => {
    it('parses valid numeric array into 4-tuple [xmin, ymin, xmax, ymax]', () => {
      expect(toBox([10, 20, 100, 200])).toEqual([10, 20, 100, 200]);
    });

    it('returns null for non-arrays or incomplete arrays', () => {
      expect(toBox(null)).toBeNull();
      expect(toBox([10, 20])).toBeNull();
      expect(toBox(['invalid', 'box', 'data', 'none'])).toBeNull();
    });
  });

  describe('DetectionOverlay component', () => {
    it('renders image and sets up canvas overlay when image loads', async () => {
      const { container } = render(
        <DetectionOverlay
          imageUrl="http://localhost:8000/uploads/shelf.jpg"
          boxes={[[10, 20, 100, 200]]}
          confidences={[0.92]}
        />
      );

      const img = screen.getByAltText(/shelf capture/i) as HTMLImageElement;
      expect(img).toBeInTheDocument();

      // Trigger image onLoad to execute canvas drawing
      Object.defineProperty(img, 'naturalWidth', { value: 800, configurable: true });
      Object.defineProperty(img, 'naturalHeight', { value: 600, configurable: true });
      fireEvent.load(img);

      const canvas = container.querySelector('canvas') as HTMLCanvasElement;
      expect(canvas).toBeInTheDocument();
      expect(canvas.width).toBe(800);
      expect(canvas.height).toBe(600);
    });

    it('renders fallback when image fails to load', async () => {
      render(
        <DetectionOverlay
          imageUrl="http://localhost:8000/uploads/corrupted.jpg"
          boxes={[]}
          confidences={[]}
        />
      );

      const img = screen.getByAltText(/shelf capture/i);
      fireEvent.error(img);

      expect(screen.getByText(/capture image could not be loaded/i)).toBeInTheDocument();
    });
  });

  describe('ShelfDetail Page rendering', () => {
    it('renders error for invalid report id in URL param', async () => {
      render(
        <MemoryRouter initialEntries={['/reports/invalid-id']}>
          <Routes>
            <Route path="/reports/:id" element={<ShelfDetail />} />
          </Routes>
        </MemoryRouter>
      );

      expect(screen.getByText(/"invalid-id" is not a valid report id/i)).toBeInTheDocument();
    });

    it('loads and renders complete report data with LLM summary and action', async () => {
      const mockReport: client.ShelfReport = {
        id: 7,
        capture_id: 25,
        fill_percentage: 0.18,
        low_confidence: false,
        empty_regions_json: [[10, 20, 100, 200], [110, 20, 200, 200]],
        urgency: 'high',
        llm_summary: 'Critically depleted shelves in Aisle 4.',
        recommended_action: 'Restock immediately from warehouse pallet #3.',
        created_at: '2026-09-13T12:00:00Z',
        camera_id: 3,
        camera_name: 'cam-snack-01',
        aisle: 'Aisle 4 - Snacks',
        store_id: 1,
        store_name: 'Downtown Flagship',
      };

      const mockCapture: client.CaptureReport = {
        capture_id: 25,
        status: 'completed',
        image_url: '/uploads/shelf_capture_25.jpg',
        captured_at: '2026-09-13T12:00:00Z',
        fill_percentage: 0.18,
        low_confidence: false,
        empty_region_count: 2,
        previous_fill: 0.60,
        report: {
          summary: 'Critically depleted shelves in Aisle 4.',
          urgency: 'high',
          recommended_action: 'Restock immediately from warehouse pallet #3.',
        },
        detections: [
          { id: 1, capture_id: 25, class: 'empty', bbox: [10, 20, 100, 200], confidence: 0.94 },
          { id: 2, capture_id: 25, class: 'empty', bbox: [110, 20, 200, 200], confidence: 0.89 },
        ],
        error_message: null,
      };

      vi.mocked(client.getReport).mockResolvedValueOnce(mockReport);
      vi.mocked(client.pollCaptureReport).mockResolvedValueOnce(mockCapture);

      render(
        <MemoryRouter initialEntries={['/reports/7']}>
          <Routes>
            <Route path="/reports/:id" element={<ShelfDetail />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('Downtown Flagship · Aisle 4 - Snacks')).toBeInTheDocument();
      });

      expect(screen.getByText('18%')).toBeInTheDocument();
      expect(screen.getByText(/previous capture: 60%/i)).toBeInTheDocument();
      expect(screen.getByText('Critically depleted shelves in Aisle 4.')).toBeInTheDocument();
      expect(
        screen.getByText('Restock immediately from warehouse pallet #3.')
      ).toBeInTheDocument();
      expect(screen.getByText(/2 empty regions outlined in red/i)).toBeInTheDocument();
    });

    it('shows a low-confidence capture as Unable to assess rather than 100% full', async () => {
      const mockReport: client.ShelfReport = {
        id: 8,
        capture_id: 30,
        fill_percentage: 1.0,
        low_confidence: true,
        empty_regions_json: [],
        urgency: 'low',
        llm_summary:
          'No empty-space detections were found in this capture, so its fill level could not be assessed.',
        recommended_action: 'Re-capture the aisle.',
        created_at: '2026-10-07T05:45:00Z',
        camera_id: 6,
        camera_name: 'cam-front',
        aisle: 'gift-shop',
        store_id: 3,
        store_name: 'xyz',
      };

      const mockCapture: client.CaptureReport = {
        capture_id: 30,
        status: 'completed',
        image_url: '/uploads/empty_shelf.png',
        captured_at: '2026-10-07T05:45:00Z',
        fill_percentage: 1.0,
        low_confidence: true,
        empty_region_count: 0,
        previous_fill: null,
        report: {
          summary:
            'No empty-space detections were found in this capture, so its fill level could not be assessed.',
          urgency: 'low',
          recommended_action: 'Re-capture the aisle.',
        },
        detections: [],
        error_message: null,
      };

      vi.mocked(client.getReport).mockResolvedValueOnce(mockReport);
      vi.mocked(client.pollCaptureReport).mockResolvedValueOnce(mockCapture);

      render(
        <MemoryRouter initialEntries={['/reports/8']}>
          <Routes>
            <Route path="/reports/:id" element={<ShelfDetail />} />
          </Routes>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByText('xyz · gift-shop')).toBeInTheDocument();
      });

      // No fabricated percentage anywhere.
      expect(screen.queryByText('100%')).not.toBeInTheDocument();
      expect(screen.queryByText('0%')).not.toBeInTheDocument();
      expect(screen.getByText('—')).toBeInTheDocument();
      // The distinct amber badge in the header ...
      expect(screen.getByText('Unable to assess')).toBeInTheDocument();
      // ... and the fill panel withholds the number too.
      expect(screen.getByText('Not assessed')).toBeInTheDocument();
      // The "no empty regions" line must read as uncertainty, not confirmation.
      expect(screen.getByText(/not that it is confirmed full/i)).toBeInTheDocument();
    });
  });
});
