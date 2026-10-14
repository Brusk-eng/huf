// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/services/whatsappApi', async (orig) => {
  const actual = await orig<typeof import('@/services/whatsappApi')>();
  return {
    ...actual,
    createTemplate: vi.fn(),
    createTemplateDraft: vi.fn(),
    editTemplate: vi.fn(),
    uploadPublicFile: vi.fn(),
  };
});

import { createTemplate, createTemplateDraft, editTemplate } from '@/services/whatsappApi';
import { emptyDraft, type TemplateWizardDraft } from '../template/draft';
import { mapBackendErrors } from './backendErrors';
import { addButton, buttonBlocker, switchVariableType } from './draftActions';
import { TemplateWizard } from './index';

const create = vi.mocked(createTemplate);
const draftApi = vi.mocked(createTemplateDraft);
const edit = vi.mocked(editTemplate);

const onDone = vi.fn();
const onCancel = vi.fn();

function setup(props: Partial<React.ComponentProps<typeof TemplateWizard>> = {}) {
  return render(<TemplateWizard gatewayName="gw" isAdmin onDone={onDone} onCancel={onCancel} {...(props as object)} />);
}

const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const next = () => fireEvent.click(screen.getByRole('button', { name: 'Next' }));

function pickMarketing() {
  fireEvent.click(screen.getByRole('radio', { name: /Default/ }));
  next();
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('step 1', () => {
  it('gates Next on a selectable type and disables unavailable ones', () => {
    setup();
    const nextBtn = screen.getByRole('button', { name: 'Next' });
    expect(nextBtn).toBeDisabled();
    expect(screen.getByRole('tab', { name: 'Authentication' })).toBeDisabled();
    expect(screen.getAllByText('Coming soon').length).toBeGreaterThan(0);
    expect(screen.getByRole('radio', { name: /Catalogue/ })).toBeDisabled();
    expect(screen.getByRole('link', { name: /template library/ })).toHaveAttribute('rel', 'noopener noreferrer');
    fireEvent.click(screen.getByRole('radio', { name: /Default/ }));
    expect(nextBtn).toBeEnabled();
    next();
    expect(screen.getByRole('region', { name: 'Write it' })).toBeInTheDocument();
  });
});

describe('step 2', () => {
  beforeEach(() => {
    setup();
    pickMarketing();
  });

  it('inserts a variable at the caret and asks for a sample', () => {
    const body = screen.getByLabelText('Body') as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: 'Hi , welcome' } });
    body.setSelectionRange(3, 3);
    fireEvent.click(screen.getByRole('toolbar', { name: 'Formatting' }).querySelector('button:last-child') as HTMLElement);
    expect(body.value).toBe('Hi {{1}}, welcome');
    const sample = screen.getByLabelText(/Body \{\{1\}\}/);
    fireEvent.blur(sample);
    expect(screen.getByText('Add a sample')).toBeInTheDocument();
    fireEvent.change(sample, { target: { value: 'Alex' } });
    expect(screen.queryByText('Add a sample')).toBeNull();
  });

  it('formats the selection from the toolbar', () => {
    const body = screen.getByLabelText('Body') as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: 'big news' } });
    body.setSelectionRange(0, 3);
    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));
    expect(body.value).toBe('*big* news');
  });

  it('converts variables when the type changes', () => {
    const body = screen.getByLabelText('Body') as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: 'Hi {{1}}, hello' } });
    fireEvent.change(screen.getByLabelText(/Body \{\{1\}\}/), { target: { value: 'Alex' } });
    fireEvent.click(screen.getByRole('radio', { name: 'Name variables' }));
    expect(body.value).toBe('Hi {{variable_1}}, hello');
    expect((screen.getByLabelText(/Body \{\{variable_1\}\}/) as HTMLInputElement).value).toBe('Alex');
  });

  it('shows the name error after blur and keeps Next blocked until valid', () => {
    const name = screen.getByLabelText('Template name');
    expect(screen.queryByText('Name is required.')).toBeNull();
    fireEvent.blur(name);
    expect(screen.getByText('Name is required.')).toBeInTheDocument();
    expect(name).toHaveAttribute('aria-invalid', 'true');
    next();
    expect(screen.getByRole('region', { name: 'Write it' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('suggests a slug for the name', () => {
    type('Template name', 'Order Update');
    fireEvent.click(screen.getByRole('button', { name: 'Use order_update' }));
    expect((screen.getByLabelText('Template name') as HTMLInputElement).value).toBe('order_update');
  });

  it('applies the button rules', () => {
    const add = (label: string) => {
      fireEvent.keyDown(screen.getByRole('button', { name: 'Add button' }), { key: 'Enter' });
      fireEvent.click(screen.getByRole('menuitem', { name: new RegExp(label) }));
    };
    add('Call phone number');
    expect(screen.getByRole('group', { name: /Button 1: Call phone number/ })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Add button' }), { key: 'Enter' });
    expect(screen.getByRole('menuitem', { name: /Call phone number/ })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('draft actions', () => {
  it('groups quick replies and enforces per-type limits', () => {
    let d: TemplateWizardDraft = emptyDraft();
    d = addButton(d, 'QUICK_REPLY', 'a');
    d = addButton(d, 'URL', 'b');
    d = addButton(d, 'QUICK_REPLY', 'c');
    expect(d.buttons.map((b) => b.id)).toEqual(['a', 'c', 'b']);
    d = addButton(addButton(d, 'URL', 'd'), 'PHONE_NUMBER', 'e');
    expect(buttonBlocker(d, 'URL')).toMatch(/2 website/);
    expect(buttonBlocker(d, 'PHONE_NUMBER')).toMatch(/one phone/i);
    expect(buttonBlocker(d, 'QUICK_REPLY')).toBeNull();
  });

  it('moves header and body samples when switching variable type', () => {
    const d = {
      ...emptyDraft(),
      header: { type: 'text' as const, text: 'Order {{1}}', mediaSample: '' },
      body: 'Hi {{1}} and {{2}}',
      samples: { '1': 'A', '2': 'B', 'header.1': 'H' },
    };
    const out = switchVariableType(d, 'name');
    expect(out.header.text).toBe('Order {{variable_1}}');
    expect(out.body).toBe('Hi {{variable_1}} and {{variable_2}}');
    expect(out.samples).toEqual({ variable_1: 'A', variable_2: 'B', 'header.variable_1': 'H' });
  });
});

describe('final step', () => {
  async function toReview() {
    pickMarketing();
    type('Template name', 'order_update');
    fireEvent.change(screen.getByLabelText('Body'), { target: { value: 'Your order has shipped' } });
    next();
    expect(screen.getByRole('region', { name: 'Check and send' })).toBeInTheDocument();
  }

  it('lets admins submit to WhatsApp', async () => {
    create.mockResolvedValue({ success: true, name: 'order_update' });
    setup();
    await toReview();
    expect(screen.getByText(/up to 24 hours/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Submit to WhatsApp' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ kind: 'submitted', name: 'order_update' }));
    expect(create).toHaveBeenCalledWith('gw', expect.objectContaining({ template_name: 'order_update', category: 'MARKETING' }));
    expect(draftApi).not.toHaveBeenCalled();
  });

  it('asks non-admins to request approval', async () => {
    draftApi.mockResolvedValue({ success: true, name: 'd1', status: 'Pending', draft: {} as never });
    setup({ isAdmin: false });
    await toReview();
    expect(screen.queryByRole('button', { name: 'Submit to WhatsApp' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Request approval' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ kind: 'draft', name: 'd1' }));
    expect(create).not.toHaveBeenCalled();
  });

  it('jumps back to the write step on field errors from the backend', async () => {
    create.mockResolvedValue({
      success: false,
      error: 'validation_failed',
      errors: [{ field: 'template_name', code: 'taken', message: 'That name is already used.' }],
    });
    setup();
    await toReview();
    fireEvent.click(screen.getByRole('button', { name: 'Submit to WhatsApp' }));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Write it' })).toBeInTheDocument());
    expect(within(screen.getByRole('region', { name: 'Write it' })).getAllByText('That name is already used.').length).toBeGreaterThan(0);
    expect(onDone).not.toHaveBeenCalled();
  });

  it('shows generic errors on the review step', async () => {
    create.mockResolvedValue({ success: false, error: 'request_failed', message: 'Network down' });
    setup();
    await toReview();
    fireEvent.click(screen.getByRole('button', { name: 'Submit to WhatsApp' }));
    expect(await screen.findByText('Network down')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Check and send' })).toBeInTheDocument();
  });
});

describe('edit mode', () => {
  const initial: TemplateWizardDraft = { ...emptyDraft('UTILITY'), name: 'order_update', language: 'en_US', body: 'Your order shipped' };

  it('skips the type step, locks name and language and uses editTemplate', async () => {
    edit.mockResolvedValue({ success: true, name: 'order_update', edits_left: 2 });
    setup({ initial, docName: 'tpl-1', editsLeft: 2 });
    expect(screen.getByRole('region', { name: 'Write it' })).toBeInTheDocument();
    expect(screen.getByLabelText('Template name')).toBeDisabled();
    expect(screen.getByRole('combobox', { name: /Language/ })).toBeDisabled();
    expect(screen.getByText(/Edits left: 2/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Previous' })).toBeNull();
    next();
    fireEvent.click(screen.getByRole('button', { name: 'Submit changes to WhatsApp' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ kind: 'edited', name: 'order_update' }));
    expect(edit).toHaveBeenCalledWith('tpl-1', expect.objectContaining({ template: 'Your order shipped' }));
  });
});

describe('mapBackendErrors', () => {
  it('maps backend fields onto form paths', () => {
    const draft = { ...emptyDraft(), body: 'Hi {{1}}', buttons: [addButton(emptyDraft(), 'URL', 'x').buttons[0]] };
    const out = mapBackendErrors(
      [
        { field: 'language_code', code: 'c', message: 'Bad language' },
        { field: 'sample_values', code: 'c', message: 'Bad sample' },
        { field: 'buttons.0.url', code: 'c', message: 'Bad url' },
        { field: 'category', code: 'c', message: 'Other' },
      ],
      draft,
    );
    expect(out.fields).toEqual({ language: 'Bad language', 'samples.1': 'Bad sample', 'buttons.0.url': 'Bad url' });
    expect(out.general).toEqual(['Other']);
  });
});
