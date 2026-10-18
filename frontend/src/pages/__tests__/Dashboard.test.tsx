import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router-dom';
import Dashboard from '../Dashboard';
import * as client from '../../api/client';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    listReports: vi.fn(),
    listCameras: vi.fn(),
  };
});

describe('Dashboard Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading skeleton and then report list', async () => {
    const mockReports: client.ShelfReport[] = [
      {
        id: 1,
        capture_id: 10,
        fill_percentage: 0.75,
        low_confidence: false,
        empty_regions_json: [],
        urgency: 'low',
        llm_summary: 'Well-stocked shelves',
        recommended_action: 'Routine checks only',
        created_at: '2026-09-13T10:00:00Z',
        camera_id: 1,
        camera_name: 'cam-01',
        aisle: 'Aisle 1',
        store_id: 1,
        store_name: 'Downtown Store',
      },
    ];

    vi.mocked(client.listReports).mockResolvedValueOnce({
      items: mockReports,
      total: 1,
      limit: 60,
      offset: 0,
    });
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Downtown Store · Aisle 1')).toBeInTheDocument();
    });

    expect(screen.getByText('75%')).toBeInTheDocument();
    expect(screen.getByText('Routine checks only')).toBeInTheDocument();
    expect(screen.getAllByText('low')[1]).toBeInTheDocument();
  });

  it('renders a low-confidence report as unable to assess, not 100% full', async () => {
    // Regression guard: a capture the detector could not read must never be shown as a
    // healthy 100% shelf. The API keeps a numeric fill (the detector's vacuous 1.0) but
    // flags it low_confidence, and the card must show a distinct warning instead.
    const unassessed: client.ShelfReport[] = [
      {
        id: 9,
        capture_id: 30,
        fill_percentage: 1.0,
        low_confidence: true,
        empty_regions_json: [],
        urgency: 'low',
        llm_summary: 'No empty-space detections were found, so the fill level could not be assessed.',
        recommended_action: 'Re-capture the aisle.',
        created_at: '2026-10-07T05:45:00Z',
        camera_id: 1,
        camera_name: 'cam-front',
        aisle: 'gift-shop',
        store_id: 3,
        store_name: 'xyz',
      },
    ];

    vi.mocked(client.listReports).mockResolvedValueOnce({
      items: unassessed,
      total: 1,
      limit: 60,
      offset: 0,
    });
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('xyz · gift-shop')).toBeInTheDocument();
    });

    expect(screen.getByText('—')).toBeInTheDocument();
    // The distinct amber warning badge (tooltip carries the full message) ...
    expect(screen.getByText('unable to assess')).toBeInTheDocument();
    // ... and the fill number is withheld, labelled "not assessed" instead.
    expect(screen.getByText('not assessed')).toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
    expect(screen.queryByText('full')).not.toBeInTheDocument();
  });

  it('renders empty state when no reports exist', async () => {
    vi.mocked(client.listReports).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 60,
      offset: 0,
    });
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/no shelf reports yet/i)).toBeInTheDocument();
    });
    expect(
      screen.getByText(/upload a shelf capture to run the detection pipeline/i)
    ).toBeInTheDocument();
  });

  it('renders error banner when reports fetch fails', async () => {
    vi.mocked(client.listReports).mockRejectedValueOnce(
      new Error('Failed to connect to backend server')
    );
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/could not load reports/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/failed to connect to backend server/i)).toBeInTheDocument();
  });

  it('navigates to report detail when card is clicked', async () => {
    const user = userEvent.setup();
    const mockReports: client.ShelfReport[] = [
      {
        id: 42,
        capture_id: 10,
        fill_percentage: 0.18,
        low_confidence: false,
        empty_regions_json: [],
        urgency: 'high',
        llm_summary: 'Critically low',
        recommended_action: 'Restock immediately',
        created_at: '2026-09-13T10:00:00Z',
        camera_id: 1,
        camera_name: 'cam-01',
        aisle: 'Aisle 3',
        store_id: 1,
        store_name: 'Downtown Store',
      },
    ];

    vi.mocked(client.listReports).mockResolvedValueOnce({
      items: mockReports,
      total: 1,
      limit: 60,
      offset: 0,
    });
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(screen.getByText('Downtown Store · Aisle 3')).toBeInTheDocument();
    });

    const card = screen.getByRole('button', { name: /downtown store · aisle 3/i });
    await user.click(card);

    expect(mockNavigate).toHaveBeenCalledWith('/reports/42');
  });

  it('has no store filter or store_id param — scope comes from the session', async () => {
    vi.mocked(client.listReports).mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 60,
      offset: 0,
    });
    vi.mocked(client.listCameras).mockResolvedValueOnce([]);

    render(
      <BrowserRouter>
        <Dashboard />
      </BrowserRouter>
    );

    await waitFor(() => {
      expect(client.listReports).toHaveBeenCalled();
    });

    // The backend derives the store from the bearer token, so the page must not
    // offer a store dropdown nor send a store_id with its filters.
    expect(screen.queryByLabelText(/^store$/i)).not.toBeInTheDocument();
    const filters = vi.mocked(client.listReports).mock.calls[0][0];
    expect(filters).not.toHaveProperty('store_id');
  });
});
