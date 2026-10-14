// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';

vi.mock('./TemplateSend', () => ({ TemplateSend: () => null }));
vi.mock('./MessageDetailSheet', () => ({
  MessageDetailSheet: ({ item }: { item: { name: string } | null }) => (item ? <div>detail {item.name}</div> : null),
  StatusTicks: () => null,
}));
vi.mock('@/services/whatsappApi', async (orig) => ({
  ...(await orig<typeof import('@/services/whatsappApi')>()),
  listMessageLog: vi.fn(),
  listMessageLogFilters: vi.fn(),
}));
import { listMessageLog, listMessageLogFilters } from '@/services/whatsappApi';
import { MessageLogTab } from './MessageLogTab';

const item = (name: string, event: string | null) => ({
  name, message_id: null, direction: 'in', phone: '+1555', profile_name: null, content_type: 'text', text: `msg ${name}`,
  status: 'failed', error: null, template: null, creation: '2026-10-14 10:00:00', gateway: 'GW-1', agent: null, agent_run: null, event, sent_by: 'customer',
});

function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}
const open = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <MessageLogTab gateway="GW-1" bridgeAvailable />
      <Search />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(listMessageLogFilters).mockResolvedValue({ success: true, statuses: [], agents: [], templates: [] } as never);
  vi.mocked(listMessageLog).mockResolvedValue({ success: true, items: [item('m1', 'EV-1'), item('m2', null)], next_cursor: null, has_more: false } as never);
});

describe('MessageLogTab one-shot links', () => {
  it('stays collapsed without ?send=1', async () => {
    open('/?tab=messages');
    await screen.findByText('msg m1');
    expect(screen.queryByLabelText('Recipient number')).toBeNull();
  });

  it('?send=1 opens the send panel and clears the param', async () => {
    open('/?tab=messages&gateway=GW-1&send=1');
    expect(await screen.findByLabelText('Recipient number')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('search')).not.toHaveTextContent('send='));
    expect(screen.getByTestId('search')).toHaveTextContent('gateway=GW-1');
  });

  it('?event= opens and highlights the matching row', async () => {
    open('/?tab=messages&event=EV-1');
    expect(await screen.findByText('detail m1')).toBeInTheDocument();
    expect(document.querySelector('[data-event="EV-1"]')).toHaveAttribute('aria-current', 'true');
    expect(screen.queryByText(/Event not in the loaded messages/)).toBeNull();
  });

  it('?event= that is not in the loaded page shows a hint', async () => {
    open('/?tab=messages&event=EV-404');
    expect(await screen.findByText(/Event not in the loaded messages/)).toBeInTheDocument();
    expect(screen.queryByText(/^detail/)).toBeNull();
  });
});
