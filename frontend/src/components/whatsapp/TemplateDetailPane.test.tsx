// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/services/whatsappApi', async (orig) => ({ ...(await orig<typeof import('@/services/whatsappApi')>()), getTemplate: vi.fn() }));
import { getTemplate } from '@/services/whatsappApi';
import { TemplateDetailPane } from './TemplateDetailPane';

const detail = {
  success: true,
  name: 'tpl-1',
  template_id: '1',
  template_name: 'order_update',
  language_code: 'en_US',
  category: 'UTILITY',
  correct_category: 'UTILITY',
  category_change_pending: false,
  status: 'APPROVED',
  parameter_format: 'NAMED',
  ttl_seconds: 3600,
  header_format: 'VIDEO',
  named_samples: [{ name: 'first_name', example: 'Ana' }],
  components: {
    header: { type: 'VIDEO', text: '', sample: 'https://x.example/v.mp4' },
    body: { text: 'Hi {{first_name}}, your order shipped', sample_values: [] },
    footer: null,
    buttons: [],
  },
  state: { rejected_reason: '', rejection_recommendation: '', other_info: '', quality_score: 'GREEN', last_event: '', last_event_at: null, last_meta_update: null },
  editable: true,
  edits_left: 4,
  manager_url: '',
  business_id: '',
};

beforeEach(() => vi.mocked(getTemplate).mockResolvedValue(detail as never));

describe('TemplateDetailPane', () => {
  it('shows variable style, validity, header format, named samples and a preview with the sample filled in', async () => {
    render(
      <TemplateDetailPane
        row={{ name: 'tpl-1' } as never} refreshKey={0} onSend={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} onUnavailable={vi.fn()}
      />,
    );
    expect(await screen.findByText('Name variables')).toBeInTheDocument();
    expect(screen.getByText('Video')).toBeInTheDocument();
    expect(screen.getByText('1 hour')).toBeInTheDocument();
    expect(screen.getByText('{{first_name}}')).toBeInTheDocument();
    expect(screen.getAllByText(/Hi Ana, your order shipped/).length).toBeGreaterThan(0);
    expect(screen.getByText(/4 edits left/)).toBeInTheDocument();
  });

  const pane = () => (
    <TemplateDetailPane
      row={{ name: 'tpl-1' } as never} refreshKey={0} onSend={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} onUnavailable={vi.fn()}
    />
  );

  it("says when Meta changed the template's category", async () => {
    vi.mocked(getTemplate).mockResolvedValue({ ...detail, category: 'MARKETING', previous_category: 'UTILITY', category_changed_by_meta: true } as never);
    render(pane());
    expect(await screen.findByText(/Meta changed this template's category from utility to marketing/i)).toBeInTheDocument();
  });

  it('shows no category notice when nothing was reclassified', async () => {
    render(pane());
    await screen.findByText('Name variables');
    expect(screen.queryByText(/Meta changed this template/)).toBeNull();
  });

  it('explains an edit block from a refused edit', async () => {
    vi.mocked(getTemplate).mockResolvedValue({
      ...detail, editable: false, editable_reason: 'edit_blocked', editable_message: "Meta won't let this template be edited right now.",
      edit_blocked_until: '2030-01-01T10:00:00+00:00',
    } as never);
    render(pane());
    expect(await screen.findByText(/won't let this template be edited right now/)).toBeInTheDocument();
    expect(screen.getByText(/Try again after/)).toBeInTheDocument();
  });
});
