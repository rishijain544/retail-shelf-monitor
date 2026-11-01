import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import * as client from '../../api/client';
import ForgotPasswordPage from '../ForgotPasswordPage';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, forgotPassword: vi.fn() };
});

/** The 202 body the backend sends for a known and an unknown address alike. */
const SERVER_202: client.AuthStatusResponse = {
  status: 'reset_requested',
  message: 'If that address has an account, a reset link is on its way.',
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/forgot-password']}>
      <Routes>
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/login" element={<div>LOGIN STUB</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function requestLink(email: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/email/i), email);
  await user.click(screen.getByRole('button', { name: /send reset link/i }));
}

describe('ForgotPasswordPage', () => {
  beforeEach(() => {
    vi.mocked(client.forgotPassword).mockResolvedValue(SERVER_202);
  });

  it('shows the identical message whether the address exists or not', async () => {
    const first = renderPage();
    await requestLink('owner@store.example');
    const forKnown = await screen.findByRole('status');
    const knownText = forKnown.textContent ?? '';

    expect(knownText).toMatch(/if an account exists for that email/i);
    expect(knownText).toMatch(/it expires in 30 minutes/i);
    // Nothing may name the account's state: the server cannot tell, so the page must not.
    expect(screen.queryByText(/no account|not found|isn't registered|doesn't exist/i)).toBeNull();

    first.unmount();

    // Same mocked 202 — the server's response for an unknown address is byte-identical.
    renderPage();
    await requestLink('nobody@store.example');
    const forUnknown = await screen.findByRole('status');

    expect(forUnknown.textContent).toBe(knownText);
    expect(client.forgotPassword).toHaveBeenLastCalledWith('nobody@store.example');
  });

  it('holds the resend button for 60 seconds and counts the wait down visibly', async () => {
    renderPage();
    await requestLink('owner@store.example');

    expect(await screen.findByRole('button', { name: /resend in 60s/i })).toBeDisabled();

    // The label counts down, so the wait is visible rather than a dead button. Only a
    // second is waited for here; the full minute would make the suite slow.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1100));
    });

    const counting = screen.getByRole('button', { name: /resend in \d+s/i });
    const secondsLeft = Number(/(\d+)s/.exec(counting.textContent ?? '')?.[1] ?? 'NaN');
    expect(secondsLeft).toBeGreaterThan(50);
    expect(secondsLeft).toBeLessThan(60);
    expect(counting).toBeDisabled();
    expect(client.forgotPassword).toHaveBeenCalledTimes(1);
  });

  it('gives a clear message when this connection is rate limited', async () => {
    vi.mocked(client.forgotPassword).mockRejectedValueOnce(
      new client.ApiError(
        429,
        'Too many reset attempts. Try again later.',
        'http://api.test/auth/forgot-password',
        'password_reset_rate_limited',
      ),
    );
    renderPage();

    await requestLink('owner@store.example');

    expect(await screen.findByRole('alert')).toHaveTextContent(/too many reset requests/i);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('links back to the login page', () => {
    renderPage();

    expect(screen.getByRole('link', { name: /back to login/i })).toHaveAttribute('href', '/login');
  });
});
