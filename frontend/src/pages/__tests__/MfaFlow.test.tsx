import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import App from '../../App';
import * as client from '../../api/client';
import { DEMO_EMAIL, DEMO_PASSWORD } from '../../demo';
import { beginMfaSetup, clearPendingFlow } from '../../hooks/useMfaSession';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    signup: vi.fn(),
    login: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    mfaVerify: vi.fn(),
    mfaSetupStart: vi.fn(),
    mfaSetupConfirm: vi.fn(),
    regenerateRecoveryCodes: vi.fn(),
    getMe: vi.fn(),
    listReports: vi.fn(),
    listCameras: vi.fn(),
    listStores: vi.fn(),
  };
});

// The health cards poll on their own schedule; stub the hook so these tests are only
// about the auth flow.
vi.mock('../../hooks/useHealth', () => ({
  useHealth: () => ({ health: null, loading: false, error: null, refresh: vi.fn() }),
}));

const AUTH_KEY = 'sm:auth:state';
const URL = 'http://api.test';

const USER = { id: 1, email: 'owner@example.com', store_id: 1, email_verified: true, mfa_enabled: true };
const TOKEN_BODY = {
  access_token: 'access-token',
  token_type: 'bearer',
  expires_in: 3600,
  user: USER,
};

function renderApp(initialEntries: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <App />
    </MemoryRouter>,
  );
}

/** Everything the authenticated shell needs so reaching "/" is not a false negative. */
function mockAuthenticatedShell() {
  vi.mocked(client.getMe).mockResolvedValue(USER);
  vi.mocked(client.listReports).mockResolvedValue({ items: [], total: 0, limit: 60, offset: 0 });
  vi.mocked(client.listCameras).mockResolvedValue([]);
  vi.mocked(client.listStores).mockResolvedValue([{ id: 1, name: 'North', location: null }]);
}

function storedAuth(): string | null {
  return localStorage.getItem(AUTH_KEY);
}

describe('signup → email verification', () => {
  beforeEach(() => {
    localStorage.clear();
    clearPendingFlow();
    mockAuthenticatedShell();
  });

  it('shows the verify screen after signup instead of the dashboard', async () => {
    const user = userEvent.setup();
    vi.mocked(client.signup).mockResolvedValue({
      status: 'verification_required',
      email: 'new@shop.com',
      expires_in: 600,
    });

    renderApp(['/signup']);

    await user.type(screen.getByLabelText(/store name/i), 'North');
    await user.type(screen.getByLabelText(/^email$/i), 'new@shop.com');
    await user.type(screen.getByLabelText(/^password$/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /create account/i }));

    expect(await screen.findByRole('heading', { name: /verify your email/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /shelf reports/i })).not.toBeInTheDocument();
    expect(screen.getByText(/new@shop\.com/)).toBeInTheDocument();
    // No token is issued by signup, so nothing may be persisted.
    expect(storedAuth()).toBeNull();
  });

  it('starts the resend cooldown at 60 seconds', async () => {
    const user = userEvent.setup();
    vi.mocked(client.signup).mockResolvedValue({
      status: 'verification_required',
      email: 'new@shop.com',
      expires_in: 600,
    });

    renderApp(['/signup']);
    await user.type(screen.getByLabelText(/store name/i), 'North');
    await user.type(screen.getByLabelText(/^email$/i), 'new@shop.com');
    await user.type(screen.getByLabelText(/^password$/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /create account/i }));

    const resend = await screen.findByRole('button', { name: /resend in 60s/i });
    expect(resend).toBeDisabled();
  });

  it('shows an error for a wrong or expired code', async () => {
    const user = userEvent.setup();
    vi.mocked(client.signup).mockResolvedValue({
      status: 'verification_required',
      email: 'new@shop.com',
      expires_in: 600,
    });
    vi.mocked(client.verifyEmail).mockRejectedValue(
      new client.ApiError(
        400,
        'That verification code is not valid or has expired. Request a new one.',
        `${URL}/auth/verify-email`,
      ),
    );

    renderApp(['/signup']);
    await user.type(screen.getByLabelText(/store name/i), 'North');
    await user.type(screen.getByLabelText(/^email$/i), 'new@shop.com');
    await user.type(screen.getByLabelText(/^password$/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /create account/i }));

    await user.type(await screen.findByLabelText(/verification code/i), '000000');
    await user.click(screen.getByRole('button', { name: /verify email/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/not valid or has expired/i);
    expect(storedAuth()).toBeNull();
  });
});

describe('login → second factor', () => {
  beforeEach(() => {
    localStorage.clear();
    clearPendingFlow();
    mockAuthenticatedShell();
  });

  it('asks for the authenticator code and reaches the dashboard with a correct one', async () => {
    const user = userEvent.setup();
    vi.mocked(client.login).mockResolvedValue({
      status: 'mfa_required',
      challenge_token: 'challenge-token',
      expires_in: 300,
    });
    vi.mocked(client.mfaVerify).mockResolvedValue(TOKEN_BODY);

    renderApp(['/login']);
    await user.type(screen.getByLabelText(/email/i), 'owner@example.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    const codeInput = await screen.findByLabelText(/authenticator code/i);
    expect(codeInput).toHaveFocus();
    // A password alone is not a session: nothing may be stored yet.
    expect(storedAuth()).toBeNull();

    await user.type(codeInput, '123456');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    expect(await screen.findByRole('heading', { name: /shelf reports/i })).toBeInTheDocument();
    expect(client.mfaVerify).toHaveBeenCalledWith({ challenge_token: 'challenge-token', code: '123456' });

    const stored = storedAuth();
    expect(stored).toContain('access-token');
    // …and never the challenge token, anywhere in storage.
    expect(stored).not.toContain('challenge-token');
    for (const key of Object.keys(localStorage)) {
      expect(localStorage.getItem(key)).not.toContain('challenge-token');
    }
  });

  it('can switch to a recovery code instead', async () => {
    const user = userEvent.setup();
    vi.mocked(client.login).mockResolvedValue({
      status: 'mfa_required',
      challenge_token: 'challenge-token',
      expires_in: 300,
    });
    vi.mocked(client.mfaVerify).mockResolvedValue(TOKEN_BODY);

    renderApp(['/login']);
    await user.type(screen.getByLabelText(/email/i), 'owner@example.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    await user.click(await screen.findByRole('button', { name: /use a recovery code instead/i }));
    await user.type(screen.getByLabelText(/recovery code/i), 'abcd-ef12');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    await waitFor(() => {
      expect(client.mfaVerify).toHaveBeenCalledWith({
        challenge_token: 'challenge-token',
        code: 'abcd-ef12',
      });
    });
  });

  it('explains a lockout with the wait time', async () => {
    const user = userEvent.setup();
    vi.mocked(client.login).mockResolvedValue({
      status: 'mfa_required',
      challenge_token: 'challenge-token',
      expires_in: 300,
    });
    vi.mocked(client.mfaVerify).mockRejectedValue(
      new client.ApiError(
        429,
        'Too many incorrect codes. Try again in 15 minute(s), or use a recovery code after that.',
        `${URL}/auth/mfa/verify`,
        'mfa_locked',
      ),
    );

    renderApp(['/login']);
    await user.type(screen.getByLabelText(/email/i), 'owner@example.com');
    await user.type(screen.getByLabelText(/password/i), 'correct-horse');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    await user.type(await screen.findByLabelText(/authenticator code/i), '000000');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /too many attempts\. try again in 15 minutes\./i,
    );
  });

  it('keeps the demo account on the one-click path with no MFA screen', async () => {
    const user = userEvent.setup();
    vi.mocked(client.login).mockResolvedValue({
      access_token: 'demo-token',
      token_type: 'bearer',
      expires_in: 3600,
      user: { id: 9, email: DEMO_EMAIL, store_id: 2, email_verified: true, mfa_enabled: false },
    });

    renderApp(['/login']);
    await user.click(screen.getByRole('button', { name: /try demo account/i }));

    expect(await screen.findByRole('heading', { name: /shelf reports/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(/authenticator code/i)).not.toBeInTheDocument();
    expect(client.login).toHaveBeenCalledWith({ email: DEMO_EMAIL, password: DEMO_PASSWORD });
    expect(storedAuth()).toContain('demo-token');
  });
});

describe('mandatory enrolment', () => {
  beforeEach(() => {
    localStorage.clear();
    clearPendingFlow();
    mockAuthenticatedShell();
  });

  it('redirects a mid-flow visitor away from the dashboard', async () => {
    // Only a setup token exists — no session — so the guard must bounce to /login
    // without ever rendering protected content.
    beginMfaSetup('owner@example.com', 'setup-token', 900);

    renderApp(['/']);

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /shelf reports/i })).not.toBeInTheDocument();
    expect(storedAuth()).toBeNull();
  });

  it('sends a direct visit to the enrolment page back to login without a pending token', async () => {
    renderApp(['/login/mfa-setup']);

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /set up two-factor/i })).not.toBeInTheDocument();
  });

  it('walks through setup and blocks Continue until the codes are acknowledged', async () => {
    const user = userEvent.setup();
    beginMfaSetup('owner@example.com', 'setup-token', 900);
    vi.mocked(client.mfaSetupStart).mockResolvedValue({
      secret: 'JBSWY3DPEHPK3PXP',
      otpauth_uri: 'otpauth://totp/ShelfMonitor:owner%40example.com?secret=JBSWY3DPEHPK3PXP',
      issuer: 'ShelfMonitor',
      account: 'owner@example.com',
    });
    const codes = [
      'aaaa-1111',
      'bbbb-2222',
      'cccc-3333',
      'dddd-4444',
      'eeee-5555',
      'ffff-6666',
      'gggg-7777',
      'hhhh-8888',
      'iiii-9999',
      'jjjj-0000',
    ];
    vi.mocked(client.mfaSetupConfirm).mockResolvedValue({ ...TOKEN_BODY, recovery_codes: codes });

    renderApp(['/login/mfa-setup']);

    expect(await screen.findByRole('heading', { name: /set up two-factor authentication/i })).toBeInTheDocument();
    // The setup token is in memory only.
    expect(storedAuth()).toBeNull();

    // The secret is shown for manual entry, so a phone that cannot scan still works.
    expect(await screen.findByText('JBSWY3DPEHPK3PXP')).toBeInTheDocument();

    await user.type(screen.getByLabelText(/code from your app/i), '123456');
    await user.click(screen.getByRole('button', { name: /turn on two-factor authentication/i }));

    expect(await screen.findByRole('heading', { name: /save your recovery codes/i })).toBeInTheDocument();
    const continueButton = screen.getByRole('button', { name: /continue/i });
    expect(continueButton).toBeDisabled();

    // Still not signed in until the codes are acknowledged.
    expect(storedAuth()).toBeNull();

    await user.click(screen.getByRole('checkbox', { name: /saved these codes/i }));
    expect(continueButton).toBeEnabled();

    await user.click(continueButton);
    expect(await screen.findByRole('heading', { name: /shelf reports/i })).toBeInTheDocument();
    expect(storedAuth()).toContain('access-token');
  });
});
