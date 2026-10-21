// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }));
vi.mock('@/services/gatewayAccessApi', () => ({
  listGatewayAccessEntries: vi.fn(),
  approveGatewayPairing: vi.fn(),
  revokeGatewayAccessEntry: vi.fn(),
}));
import { toast } from 'sonner';
import { approveGatewayPairing, listGatewayAccessEntries, revokeGatewayAccessEntry } from '@/services/gatewayAccessApi';
import { AccessTab } from './AccessTab';

const entry = (over: Record<string, unknown> = {}) => ({
  name: 'GAE-1', gateway: 'GW-1', provider: 'WhatsApp', entry_type: 'Sender', external_id: '15551234567',
  pairing_code: 'PAIR-ABCD2345', state: 'Pending', creation: '2026-10-21 10:00:00', ...over,
});

const list = vi.mocked(listGatewayAccessEntries);

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue([entry()] as never);
});

describe('AccessTab', () => {
  it('loads the waiting queue for the gateway first', async () => {
    render(<AccessTab gateway="GW-1" />);
    expect(await screen.findByText('15551234567')).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith({ gateway: 'GW-1', state: 'Pending' });
    expect(screen.getByText('PAIR-ABCD2345')).toBeInTheDocument();
    expect(screen.getByText('Person')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeInTheDocument();
  });

  it('shows a friendly empty state per tab', async () => {
    list.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    expect(await screen.findByText('No one is waiting for approval.')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Approved' }));
    expect(await screen.findByText('No approved senders yet.')).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith({ gateway: 'GW-1', state: 'Approved' });
    await user.click(screen.getByRole('tab', { name: 'Revoked' }));
    expect(await screen.findByText('No revoked senders.')).toBeInTheDocument();
  });

  it('approves a request, reloads and warns when the welcome message was not sent', async () => {
    vi.mocked(approveGatewayPairing).mockResolvedValue({ notification_status: 'Notification not sent' } as never);
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    expect(approveGatewayPairing).toHaveBeenCalledWith('GAE-1');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Access approved'));
    expect(toast.message).toHaveBeenCalledWith('The welcome message could not be sent.');
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('does not warn when the welcome message went out', async () => {
    vi.mocked(approveGatewayPairing).mockResolvedValue({ notification_status: 'sent' } as never);
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.message).not.toHaveBeenCalled();
  });

  it('declines a waiting request', async () => {
    vi.mocked(revokeGatewayAccessEntry).mockResolvedValue({ name: 'GAE-1', state: 'Revoked' });
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    await user.click(await screen.findByRole('button', { name: 'Decline' }));
    expect(revokeGatewayAccessEntry).toHaveBeenCalledWith('GAE-1');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Access revoked'));
  });

  it('offers Revoke, not Approve, for an approved group and nothing for a revoked entry', async () => {
    const user = userEvent.setup();
    list.mockResolvedValue([entry({ state: 'Approved', entry_type: 'Room', approved_by: 'admin@example.com', display_label: 'Team' })] as never);
    render(<AccessTab gateway="GW-1" />);
    await user.click(await screen.findByRole('tab', { name: 'Approved' }));
    expect(await screen.findByText('Group')).toBeInTheDocument();
    expect(screen.getByText('by admin@example.com')).toBeInTheDocument();
    expect(screen.getByText('Team')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Revoke' })).toBeInTheDocument();

    list.mockResolvedValue([entry({ state: 'Revoked' })] as never);
    await user.click(screen.getByRole('tab', { name: 'Revoked' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Revoke' })).toBeNull());
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });

  it('shows the error with a retry when the list fails', async () => {
    list.mockRejectedValueOnce(new Error('Permission denied'));
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Permission denied');
    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('15551234567')).toBeInTheDocument();
  });

  it('reports an action failure and keeps the row', async () => {
    vi.mocked(approveGatewayPairing).mockRejectedValue(new Error('Already handled'));
    const user = userEvent.setup();
    render(<AccessTab gateway="GW-1" />);
    await user.click(await screen.findByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Already handled'));
    expect(screen.getByText('15551234567')).toBeInTheDocument();
  });

  it('reloads for another gateway', async () => {
    const { rerender } = render(<AccessTab gateway="GW-1" />);
    await screen.findByText('15551234567');
    rerender(<AccessTab gateway="GW-2" />);
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ gateway: 'GW-2', state: 'Pending' }));
  });
});
