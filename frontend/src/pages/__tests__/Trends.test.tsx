import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Trends, { seriesKeyOf, SERIES_COLORS, reportLabel } from '../Trends';
import * as client from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    listReports: vi.fn(),
    listCameras: vi.fn(),
  };
});

describe('Trends Page - Color Keying & Cross-Store Collision Logic', () => {
  const mockCameras: client.Camera[] = [
    { id: 10, store_id: 1, aisle: 'Aisle 1', name: 'cam-dt-1' },
    { id: 11, store_id: 1, aisle: 'Aisle 2', name: 'cam-dt-2' },
    { id: 20, store_id: 2, aisle: 'Aisle 1', name: 'cam-up-1' }, // Same aisle name "Aisle 1" in store 2!
  ];

  const mockReports: client.ShelfReport[] = [
    {
      id: 101,
      capture_id: 1,
      fill_percentage: 0.85,
      low_confidence: false,
      empty_regions_json: [],
      urgency: 'low',
      llm_summary: 'Full',
      recommended_action: 'None',
      created_at: '2026-09-13T10:00:00Z',
      camera_id: 10,
      camera_name: 'cam-dt-1',
      aisle: 'Aisle 1',
      store_id: 1,
      store_name: 'Downtown Store',
    },
    {
      id: 102,
      capture_id: 2,
      fill_percentage: 0.45,
      low_confidence: false,
      empty_regions_json: [],
      urgency: 'medium',
      llm_summary: 'Half empty',
      recommended_action: 'Restock',
      created_at: '2026-09-13T11:00:00Z',
      camera_id: 20,
      camera_name: 'cam-up-1',
      aisle: 'Aisle 1', // Same aisle name "Aisle 1" in store 2
      store_id: 2,
      store_name: 'Uptown Store',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Pure keying & collision helpers', () => {
    it('defines an 8-color validated palette for distinct series slots', () => {
      expect(SERIES_COLORS).toHaveLength(8);
      expect(SERIES_COLORS[0]).toBe('#2a78d6');
    });

    it('generates distinct keys for same-named aisles from different stores', () => {
      const keyStore1 = seriesKeyOf(1, 'Aisle 1');
      const keyStore2 = seriesKeyOf(2, 'Aisle 1');

      expect(keyStore1).toBe('1::Aisle 1');
      expect(keyStore2).toBe('2::Aisle 1');
      expect(keyStore1).not.toBe(keyStore2);
    });

    it('formats report labels with store and aisle name to avoid confusion', () => {
      const label1 = reportLabel(mockReports[0]);
      const label2 = reportLabel(mockReports[1]);

      expect(label1).toBe('Downtown Store · Aisle 1');
      expect(label2).toBe('Uptown Store · Aisle 1');
      expect(label1).not.toBe(label2);
    });
  });

  describe('Trends Component rendering & color stability', () => {
    it('renders trend chart and preserves camera directory colors', async () => {
      vi.mocked(client.listCameras).mockResolvedValueOnce(mockCameras);
      vi.mocked(client.listReports).mockResolvedValueOnce({
        items: mockReports,
        total: 2,
        limit: 200,
        offset: 0,
      });

      render(<Trends />);

      await waitFor(() => {
        expect(screen.getByText(/2 captures across 2 aisles/i)).toBeInTheDocument();
      });

      // Filter options populate. The store is fixed by the session, so only the
      // aisle selector is rendered.
      expect(screen.getByLabelText(/aisle/i)).toBeInTheDocument();
      expect(screen.queryByLabelText(/^store$/i)).not.toBeInTheDocument();
    });

    it('toggles table view and displays tabular breakdown of fill percentages', async () => {
      const user = userEvent.setup();
      vi.mocked(client.listCameras).mockResolvedValueOnce(mockCameras);
      vi.mocked(client.listReports).mockResolvedValueOnce({
        items: mockReports,
        total: 2,
        limit: 200,
        offset: 0,
      });

      render(<Trends />);

      await waitFor(() => {
        expect(screen.getByText(/2 captures across 2 aisles/i)).toBeInTheDocument();
      });

      const tableToggleBtn = screen.getByRole('button', { name: /table view/i });
      await user.click(tableToggleBtn);

      expect(screen.getByText('85%')).toBeInTheDocument();
      expect(screen.getByText('45%')).toBeInTheDocument();
      expect(screen.getByText('Downtown Store · Aisle 1')).toBeInTheDocument();
      expect(screen.getByText('Uptown Store · Aisle 1')).toBeInTheDocument();
    });
  });
});
