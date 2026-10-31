import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import * as client from '../../api/client';
import Security from '../Security';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, regenerateRecoveryCodes: vi.fn() };
});

const AUTH_KEY = 'sm:auth:state';

function seedAuth(mfaEnabled: boolean) {
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({
      kind: 'authenticated',
      user: {
        id: 1,
        email: 'owner@example.com',
        store_id: 1,
        email_verified: true,
        mfa_enabled: mfaEnabled,
      },
      token: 'access-token',
    }),
  );
}

function renderSecurity() {
  return render(
    <MemoryRouter>
      <Security />
    </MemoryRouter>,
  );
}

describe('Security page', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('reports that two-factor is on for an enrolled account', () => {
    seedAuth(true);
    renderSecurity();

    expect(screen.getByText(/two-factor authentication: on/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /regenerate recovery codes/i }),
    ).toBeInTheDocument();
  });

  it('does not offer recovery-code management to an account without an authenticator', () => {
    seedAuth(false);
    renderSecurity();

    expect(screen.getByText(/two-factor authentication: off/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /regenerate recovery codes/i }),
    ).not.toBeInTheDocument();
  });

  it('requires both factors, then shows the new codes once', async () => {
    const user = userEvent.setup();
    seedAuth(true);
    vi.mocked(client.regenerateRecoveryCodes).mockResolvedValue({
      status: 'recovery_codes_regenerated',
      recovery_codes: ['aaaa-1111', 'bbbb-2222'],
    });

    renderSecurity();

    await user.type(screen.getByLabelText(/^password$/i), 'correct-horse');
    await user.type(screen.getByLabelText(/authenticator code/i), '123456');
    await user.click(screen.getByRole('button', { name: /regenerate recovery codes/i }));

    expect(client.regenerateRecoveryCodes).toHaveBeenCalledWith({
      password: 'correct-horse',
      code: '123456',
    });

    // The old codes are gone server-side, so the new ones must be acknowledged.
    expect(await screen.findByText('aaaa-1111')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /done/i })).toBeDisabled();

    await user.click(screen.getByRole('checkbox', { name: /saved these codes/i }));
    expect(screen.getByRole('button', { name: /done/i })).toBeEnabled();
  });
});
