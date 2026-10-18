import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import HealthCards from '../HealthCards';
import type { HealthData } from '../../api/client';

function makeHealth(
  overrides: { database?: string; alert_email?: string; alert_email_transport?: string } = {},
): HealthData {
  return {
    status: 'healthy',
    services: {
      api: 'healthy',
      database: overrides.database ?? 'healthy',
      alert_slack: 'healthy',
      alert_email: overrides.alert_email ?? 'not_configured',
      alert_email_transport: overrides.alert_email_transport ?? 'none',
    },
  };
}

describe('HealthCards', () => {
  it('shows Healthy for a reachable database', () => {
    render(<HealthCards health={makeHealth()} error={null} />);
    expect(screen.getByText('Healthy')).toBeInTheDocument();
    expect(screen.queryByText('Connecting...')).not.toBeInTheDocument();
    expect(screen.queryByText('Down')).not.toBeInTheDocument();
    // Redis was removed from the stack — its card must be gone too.
    expect(screen.queryByText('Redis Cache')).not.toBeInTheDocument();
  });

  it('shows Down when the database is unhealthy', () => {
    render(
      <HealthCards health={makeHealth({ database: 'unhealthy: OperationalError' })} error={null} />,
    );
    expect(screen.getByText('Down')).toBeInTheDocument();
    expect(screen.getByText('Down')).toHaveAttribute('title', 'unhealthy: OperationalError');
    expect(screen.queryByText('Connecting...')).not.toBeInTheDocument();
  });

  it('shows Connecting... only while a service status is genuinely unknown', () => {
    render(<HealthCards health={makeHealth({ database: 'unknown' })} error={null} />);
    expect(screen.getByText('Connecting...')).toBeInTheDocument();
  });

  it('falls back to a Down state for the dependencies when /health itself fails', () => {
    render(<HealthCards health={null} error="Failed to fetch" />);
    expect(screen.getByText('Disconnected')).toBeInTheDocument();
    // Both dependency cards (database + alert email) report the outage.
    expect(screen.getAllByText('Down')).toHaveLength(2);
    expect(screen.queryByText('Connecting...')).not.toBeInTheDocument();
  });

  it('flags a missing email transport so "alert raised, nothing sent" is explainable', () => {
    render(<HealthCards health={makeHealth()} error={null} />);

    expect(screen.getByText('Alert Email')).toBeInTheDocument();
    const badge = screen.getByText('Not configured');
    expect(badge).toHaveAttribute(
      'title',
      expect.stringContaining('Email setup'),
    );
  });

  it('shows Not working (not Configured) when the provider rejects the saved key', () => {
    render(
      <HealthCards
        health={makeHealth({
          alert_email:
            'unhealthy: the email provider rejected the API key (HTTP 401); create a new key and save it again',
          alert_email_transport: 'api',
        })}
        error={null}
      />,
    );

    const badge = screen.getByText('Not working');
    expect(badge).toHaveAttribute('title', expect.stringContaining('rejected the API key'));
    expect(screen.queryByText('Configured')).not.toBeInTheDocument();
  });

  it('shows Configured and names the transport once email delivery is set up', () => {
    render(
      <HealthCards
        health={makeHealth({ alert_email: 'healthy', alert_email_transport: 'api' })}
        error={null}
      />,
    );

    expect(screen.getByText('Configured')).toHaveAttribute(
      'title',
      expect.stringContaining('email API key'),
    );
    expect(screen.queryByText('Not configured')).not.toBeInTheDocument();
  });
});
