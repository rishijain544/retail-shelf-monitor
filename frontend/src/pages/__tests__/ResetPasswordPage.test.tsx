import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter, Route, Routes, useLocation } from 'react-router-dom';

import * as client from '../../api/client';
import ResetPasswordPage from '../ResetPasswordPage';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, resetPassword: vi.fn() };
});

const TOKEN = 'tok_abcdefghijklmnopqrstuvwxyz';
const NEW_PASSWORD = 'new-password-1';

/** Stand-in for the login page, showing the one-time notice it was handed. */
function LoginStub() {
  const location = useLocation();
  const notice = (location.state as { notice?: string } | null)?.notice;
  return (
    <div>
      <p>LOGIN STUB</p>
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}

function renderPage() {
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/forgot-password" element={<div>FORGOT STUB</div>} />
        <Route path="/login" element={<LoginStub />} />
      </Routes>
    </BrowserRouter>,
  );
}

/**
 * Submit the form directly.
 *
 * The inputs carry `minLength`, and jsdom enforces constraint validation for a real
 * click — which would stop the weak-password case before it reached the handler. The
 * behaviour under test is the page's own check, so the submit is dispatched directly.
 */
function submitForm() {
  const form = screen.getByLabelText(/^new password$/i).closest('form');
  if (!form) throw new Error('reset form not found');
  fireEvent.submit(form);
}

async function fillPasswords(password: string, confirmation: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/^new password$/i), password);
  await user.type(screen.getByLabelText(/^confirm new password$/i), confirmation);
}

describe('ResetPasswordPage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    window.history.pushState({}, '', `/reset-password?token=${TOKEN}`);
    vi.mocked(client.resetPassword).mockResolvedValue({
      status: 'password_reset',
      message: 'Your password has been changed. Sign in with your new password.',
    });
  });

  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  it('reads the token and removes it from the address bar immediately', () => {
    renderPage();

    expect(window.location.pathname).toBe('/reset-password');
    expect(window.location.search).toBe('');
    expect(window.location.href).not.toContain(TOKEN);
  });

  it('blocks two different passwords before calling the API', async () => {
    renderPage();
    await fillPasswords(NEW_PASSWORD, 'new-password-2');

    submitForm();

    expect(await screen.findByRole('alert')).toHaveTextContent(/passwords do not match/i);
    expect(client.resetPassword).not.toHaveBeenCalled();
  });

  it('blocks a password that does not meet the signup rule', async () => {
    renderPage();
    await fillPasswords('short', 'short');

    submitForm();

    expect(await screen.findByRole('alert')).toHaveTextContent(/must be at least 8 characters/i);
    expect(client.resetPassword).not.toHaveBeenCalled();
  });

  it('blocks a password over the 72-byte limit even when it is short enough in characters', async () => {
    renderPage();
    // 40 characters, but 120 bytes: bcrypt would read only the first 72.
    const multibyte = '\u3042'.repeat(40);
    await fillPasswords(multibyte, multibyte);

    submitForm();

    expect(await screen.findByRole('alert')).toHaveTextContent(/72 bytes|too long/i);
    expect(client.resetPassword).not.toHaveBeenCalled();
  });

  it('replaces the form with a friendly message when the link is invalid or expired', async () => {
    vi.mocked(client.resetPassword).mockRejectedValueOnce(
      new client.ApiError(
        400,
        'That reset link is invalid or has expired. Request a new one.',
        'http://api.test/auth/reset-password',
        'reset_token_invalid',
      ),
    );
    renderPage();
    await fillPasswords(NEW_PASSWORD, NEW_PASSWORD);

    submitForm();

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or has expired/i);
    expect(screen.queryByLabelText(/^new password$/i)).toBeNull();
    expect(screen.getByRole('link', { name: /request a new link/i })).toHaveAttribute(
      'href',
      '/forgot-password',
    );
  });

  it('explains a link that arrived without a token', () => {
    window.history.pushState({}, '', '/reset-password');
    renderPage();

    expect(screen.getByRole('alert')).toHaveTextContent(/missing its reset code/i);
    expect(screen.getByRole('link', { name: /request a new link/i })).toHaveAttribute(
      'href',
      '/forgot-password',
    );
  });

  it('sends the visitor to login without signing them in or writing to storage', async () => {
    renderPage();
    await fillPasswords(NEW_PASSWORD, NEW_PASSWORD);

    submitForm();

    expect(await screen.findByText('LOGIN STUB')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Password updated. Please log in.');
    expect(client.resetPassword).toHaveBeenCalledWith(TOKEN, NEW_PASSWORD);

    // Nothing was persisted: no session token, and the reset token is not in any storage.
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(JSON.stringify({ ...localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(TOKEN);
  });
});
