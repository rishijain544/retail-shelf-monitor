import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import * as client from '../../api/client';
import { DEMO_EMAIL, DEMO_PASSWORD } from '../../demo';
import LoginPage from '../LoginPage';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, login: vi.fn() };
});

const AUTH_KEY = 'sm:auth:state';

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* Stand-in for the authenticated shell, to prove the redirect happens. */}
        <Route path="/" element={<div>DASHBOARD STUB</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('LoginPage demo access', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(client.login).mockResolvedValue({
      access_token: 'demo-token',
      token_type: 'bearer',
      expires_in: 3600,
      user: { id: 1, email: DEMO_EMAIL, store_id: 1 },
    });
  });

  it('shows the demo credentials in plain text so they can be typed manually later', () => {
    renderLogin();

    expect(screen.getByText(DEMO_EMAIL)).toBeInTheDocument();
    expect(screen.getByText(DEMO_PASSWORD)).toBeInTheDocument();
  });

  it('signs in with one click, filling the form and storing the session', async () => {
    const user = userEvent.setup();

    // Hold the response open so the auto-filled form can be inspected before the
    // redirect unmounts the login page.
    let settle!: (value: client.AuthTokenResponse) => void;
    vi.mocked(client.login).mockImplementationOnce(
      () =>
        new Promise<client.AuthTokenResponse>((resolve) => {
          settle = resolve;
        }),
    );

    renderLogin();
    await user.click(screen.getByRole('button', { name: /try demo account/i }));

    // No typing: the button supplies the demo credentials itself.
    expect(client.login).toHaveBeenCalledWith({ email: DEMO_EMAIL, password: DEMO_PASSWORD });

    // The visible fields are auto-filled, so it is obvious which account was used.
    expect(screen.getByLabelText(/email/i)).toHaveValue(DEMO_EMAIL);
    expect(screen.getByLabelText(/password/i)).toHaveValue(DEMO_PASSWORD);

    settle({
      access_token: 'demo-token',
      token_type: 'bearer',
      expires_in: 3600,
      user: { id: 1, email: DEMO_EMAIL, store_id: 1 },
    });

    // A persisted session means the gated app will load straight into the dashboard.
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(AUTH_KEY) ?? 'null');
      expect(stored?.kind).toBe('authenticated');
      expect(stored?.token).toBe('demo-token');
    });
    expect(await screen.findByText('DASHBOARD STUB')).toBeInTheDocument();
  });

  it('surfaces a failed demo sign-in instead of silently doing nothing', async () => {
    const user = userEvent.setup();
    vi.mocked(client.login).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    renderLogin();

    await user.click(screen.getByRole('button', { name: /try demo account/i }));

    expect(await screen.findByText(/server could not be reached/i)).toBeInTheDocument();
    expect(screen.queryByText('DASHBOARD STUB')).not.toBeInTheDocument();
  });
});
