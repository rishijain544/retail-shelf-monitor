import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import App from '../App';
import { ApiError, getMe, listCameras, listReports, listStores } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    getMe: vi.fn(),
    listReports: vi.fn(),
    listCameras: vi.fn(),
    listStores: vi.fn(),
  };
});

// The health cards poll on their own schedule; stub the hook so these tests are
// only about routing and session scoping.
vi.mock('../hooks/useHealth', () => ({
  useHealth: () => ({ health: null, loading: false, error: null, refresh: vi.fn() }),
}));

const AUTH_KEY = 'sm:auth:state';

function seedAuth(): void {
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({
      kind: 'authenticated',
      user: { id: 1, email: 'owner@example.com', store_id: 7 },
      token: 'stub-token',
    }),
  );
}

describe('App route guard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('redirects unauthenticated visitors to the login page', async () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText('Shelf Reports')).not.toBeInTheDocument();
  });

  it('clears the session and redirects when the stored token is rejected', async () => {
    seedAuth();
    // The persisted token looks valid to the client; the server rejects it.
    vi.mocked(getMe).mockRejectedValue(new ApiError(401, 'Invalid or expired token', '/auth/me'));
    vi.mocked(listReports).mockResolvedValue({ items: [], total: 0, limit: 60, offset: 0 });
    vi.mocked(listCameras).mockResolvedValue([]);
    vi.mocked(listStores).mockResolvedValue([]);

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText('Shelf Reports')).not.toBeInTheDocument();
    expect(getMe).toHaveBeenCalled();
    // The dead token must not linger, or every reload would retry it.
    expect(localStorage.getItem(AUTH_KEY)).toBeNull();
  });

  it('renders the shell plus the session store name when authenticated', async () => {
    seedAuth();
    vi.mocked(getMe).mockResolvedValue({
      id: 1,
      email: 'owner@example.com',
      store_id: 7,
    });
    vi.mocked(listReports).mockResolvedValue({ items: [], total: 0, limit: 60, offset: 0 });
    vi.mocked(listCameras).mockResolvedValue([]);
    vi.mocked(listStores).mockResolvedValue([
      { id: 7, name: 'Downtown Store', location: '100 Main St' },
    ]);

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: /shelf reports/i })).toBeInTheDocument();
    expect(screen.getByText('Signed in as')).toBeInTheDocument();
    expect(await screen.findByText('Downtown Store')).toBeInTheDocument();
  });
});
