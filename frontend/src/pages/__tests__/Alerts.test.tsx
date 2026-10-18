import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Alerts from '../Alerts';
import * as client from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    listAlerts: vi.fn(),
    sendTestAlert: vi.fn(),
    getEmailConfig: vi.fn(),
    saveEmailConfig: vi.fn(),
  };
});

describe('Alerts Page - Sorting & Filtering Logic', () => {
  const mockAlerts: client.Alert[] = [
    {
      id: 1,
      shelf_report_id: 101,
      sent_to_slack: true,
      sent_to_email: true,
      email_recipients: 'owner@downtown.test',
      sent_at: '2026-09-10T10:00:00Z',
      camera_id: 1,
      camera_name: 'cam-01',
      aisle: 'Aisle 1 - Beverages',
      store_id: 1,
      store_name: 'Downtown Store',
      urgency: 'high',
      llm_summary: 'Critically low stock',
      report_created_at: '2026-09-10T10:00:00Z',
    },
    {
      id: 2,
      shelf_report_id: 102,
      sent_to_slack: false,
      sent_to_email: false,
      email_recipients: null,
      sent_at: null,
      camera_id: 2,
      camera_name: 'cam-02',
      aisle: 'Aisle 4 - Snacks',
      store_id: 1,
      store_name: 'Downtown Store',
      urgency: 'medium',
      llm_summary: 'Moderate stock level',
      report_created_at: '2026-09-12T12:00:00Z',
    },
    {
      id: 3,
      shelf_report_id: 103,
      sent_to_slack: true,
      sent_to_email: true,
      email_recipients: 'owner@uptown.test',
      sent_at: '2026-09-13T08:00:00Z',
      camera_id: 3,
      camera_name: 'cam-03',
      aisle: 'Aisle 2 - Dairy',
      store_id: 2,
      store_name: 'Uptown Store',
      urgency: 'high',
      llm_summary: 'Urgent refill required',
      report_created_at: '2026-09-13T08:00:00Z',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    // EmailSetupButton checks the saved config on mount; default to "not configured"
    // so the setup form renders as before.
    vi.mocked(client.getEmailConfig).mockResolvedValue({
      configured: false,
      transport: 'none',
      from_address: 'ShelfMonitor <onboarding@resend.dev>',
      last_error: null,
      key: null,
      detail: null,
      email_delivered_to: [],
      alert_email_to: null,
      smtp_host: null,
      smtp_user: null,
    });
  });

  it('renders alerts and shows 200-row cap footer note when total exceeds loaded rows', async () => {
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 250, // total > items.length
      limit: 200,
      offset: 0,
    });

    render(<Alerts />);

    await waitFor(() => {
      expect(screen.getByText(/showing the 3 most recent alerts of 250/i)).toBeInTheDocument();
    });

    expect(screen.getByText('Critically low stock')).toBeInTheDocument();
    expect(screen.getByText('Moderate stock level')).toBeInTheDocument();
    expect(screen.getByText('Urgent refill required')).toBeInTheDocument();
  });

  it('shows email delivery alongside the Slack status per alert', async () => {
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });

    render(<Alerts />);

    await waitFor(() => {
      expect(screen.getByText('Critically low stock')).toBeInTheDocument();
    });

    // One header per channel, and the delivered-alert row exposes the recipient.
    expect(screen.getByRole('columnheader', { name: /slack/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /email/i })).toBeInTheDocument();
    expect(screen.getByTitle('Sent to owner@downtown.test')).toBeInTheDocument();
  });

  it('sorts by urgency correctly (high > medium > low) and toggles direction with ID tie-breaking', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });

    render(<Alerts />);

    await waitFor(() => {
      expect(screen.getByText('Critically low stock')).toBeInTheDocument();
    });

    const urgencyHeaderBtn = screen.getByRole('button', { name: /urgency/i });

    // Click to sort by urgency (descending by default on first click: High -> Medium -> Low)
    await user.click(urgencyHeaderBtn);

    const rows = screen.getAllByRole('row');
    // Header row is index 0. Row 1 and 2 should be 'high', Row 3 'medium'
    expect(rows[1]).toHaveTextContent(/high/i);
    expect(rows[2]).toHaveTextContent(/high/i);
    expect(rows[3]).toHaveTextContent(/medium/i);

    // Click again to toggle ascending (Low/Medium -> High)
    await user.click(urgencyHeaderBtn);
    const ascRows = screen.getAllByRole('row');
    expect(ascRows[1]).toHaveTextContent(/medium/i);
    expect(ascRows[2]).toHaveTextContent(/high/i);
  });

  it('filters alerts by urgency dropdown and date range, and clear filters resets view', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });

    render(<Alerts />);

    await waitFor(() => {
      expect(screen.getByText('Critically low stock')).toBeInTheDocument();
    });

    // 1. Filter by urgency: 'medium'
    const urgencySelect = screen.getByLabelText(/urgency/i);
    await user.selectOptions(urgencySelect, 'medium');

    expect(screen.getByText('Moderate stock level')).toBeInTheDocument();
    expect(screen.queryByText('Critically low stock')).not.toBeInTheDocument();
    expect(screen.queryByText('Urgent refill required')).not.toBeInTheDocument();

    // 2. Clear filters
    const clearBtn = screen.getByRole('button', { name: /clear filters/i });
    await user.click(clearBtn);

    expect(screen.getByText('Critically low stock')).toBeInTheDocument();
    expect(screen.getByText('Moderate stock level')).toBeInTheDocument();
    expect(screen.getByText('Urgent refill required')).toBeInTheDocument();

    // 3. Filter by date: from 2026-09-12 to 2026-09-13
    const fromInput = screen.getByLabelText(/from/i);
    const toInput = screen.getByLabelText(/to/i);
    await user.type(fromInput, '2026-09-12');
    await user.type(toInput, '2026-09-13');

    expect(screen.getByText('Moderate stock level')).toBeInTheDocument();
    expect(screen.getByText('Urgent refill required')).toBeInTheDocument();
    expect(screen.queryByText('Critically low stock')).not.toBeInTheDocument();
  });

  it('shows which channels carried the test alert, including an unconfigured one', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });
    vi.mocked(client.sendTestAlert).mockResolvedValueOnce({
      status: 'sent',
      detail: 'Test alert delivered via Slack.',
      slack: 'sent',
      email: 'not_configured',
      email_recipients: [],
    });

    render(<Alerts />);

    await user.click(screen.getByRole('button', { name: /send test alert/i }));

    await waitFor(() => {
      expect(screen.getByText('Test alert delivered via Slack.')).toBeInTheDocument();
    });
    // Slack delivering must not imply email did: the email chip says why it didn't.
    expect(screen.getByText(/Email:not configured/)).toHaveAttribute(
      'title',
      expect.stringContaining('Email setup'),
    );
  });

  it('verifies and saves a pasted email key from the Email setup panel', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });
    vi.mocked(client.saveEmailConfig).mockResolvedValueOnce({
      configured: true,
      transport: 'api',
      from_address: 'ShelfMonitor <onboarding@resend.dev>',
      last_error: null,
      key: 're_abc…1234',
      detail: 'key accepted by the email provider',
      email_delivered_to: ['owner@downtown.test'],
      alert_email_to: null,
      smtp_host: null,
      smtp_user: null,
    });

    render(<Alerts />);

    await user.click(screen.getByRole('button', { name: /email setup/i }));
    // The setup form defaults to the SMTP tab (it reaches every login email); switch
    // to the Resend API tab to exercise the API-key path.
    await user.click(screen.getByRole('button', { name: /resend api/i }));
    await user.type(screen.getByPlaceholderText('re_…'), 're_validkey1234567890');
    await user.click(screen.getByRole('button', { name: /save & send test/i }));

    await waitFor(() => {
      expect(client.saveEmailConfig).toHaveBeenCalledWith({
        api_key: 're_validkey1234567890',
        from_email: null,
        alert_email_to: null,
      });
    });
    expect(await screen.findByText(/Test email delivered to owner@downtown.test/)).toBeInTheDocument();
  });

  it('surfaces a rejected key instead of claiming email is configured', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });
    vi.mocked(client.saveEmailConfig).mockRejectedValueOnce(
      new client.ApiError(
        400,
        'the email provider rejected this key (invalid or revoked)',
        '/alerts/email-config',
      ),
    );

    render(<Alerts />);

    await user.click(screen.getByRole('button', { name: /email setup/i }));
    // Switch to the Resend API tab to exercise the API-key path.
    await user.click(screen.getByRole('button', { name: /resend api/i }));
    await user.type(screen.getByPlaceholderText('re_…'), 're_revokedkey1234567890');
    await user.click(screen.getByRole('button', { name: /save & send test/i }));

    expect(
      await screen.findByText(/rejected this key/i),
    ).toBeInTheDocument();
  });

  it('triggers send test alert and renders success notification', async () => {
    const user = userEvent.setup();
    vi.mocked(client.listAlerts).mockResolvedValueOnce({
      items: mockAlerts,
      total: 3,
      limit: 200,
      offset: 0,
    });
    vi.mocked(client.sendTestAlert).mockResolvedValueOnce({
      status: 'sent',
      detail: 'Diagnostic test delivered to #shelf-alerts',
    });

    render(<Alerts />);

    const sendTestBtn = screen.getByRole('button', { name: /send test alert/i });
    await user.click(sendTestBtn);

    await waitFor(() => {
      expect(screen.getByText('Diagnostic test delivered to #shelf-alerts')).toBeInTheDocument();
    });
  });
});
