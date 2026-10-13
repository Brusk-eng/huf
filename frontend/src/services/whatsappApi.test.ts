// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
  buildTemplatePayload,
  errorText,
  extractPlaceholders,
  isBridgeUnavailable,
  unwrapResult,
  validateTemplateForm,
} from './whatsappApi';

describe('whatsappApi helpers', () => {
  it('extracts ordered unique placeholders', () => {
    expect(extractPlaceholders('Hi {{1}}, order {{2}} {{1}}')).toEqual([1, 2]);
    expect(extractPlaceholders('none')).toEqual([]);
  });

  it('validates template forms', () => {
    const ok = { name: 'order_update', language: 'en_US', body: 'Hi {{1}}', samples: ['Ann'] };
    expect(validateTemplateForm(ok)).toBeNull();
    expect(validateTemplateForm({ ...ok, name: 'Bad Name' })).toMatch(/Name/);
    expect(validateTemplateForm({ ...ok, body: 'Hi {{2}}' })).toMatch(/Placeholders/);
    expect(validateTemplateForm({ ...ok, samples: [''] })).toMatch(/sample/);
    expect(validateTemplateForm({ ...ok, body: ' ' })).toMatch(/Body/);
  });

  it('builds payload, dropping empty optionals', () => {
    const p = buildTemplatePayload({
      name: 'a_b', category: 'UTILITY', language: 'en_US', body: 'Hi {{1}}', header: ' ', footer: 'Bye', samples: ['Ann', 'x'],
    });
    expect(p).toEqual({
      template_name: 'a_b', category: 'UTILITY', language_code: 'en_US', template: 'Hi {{1}}', footer: 'Bye', sample_values: 'Ann',
    });
  });

  it('unwraps results and detects bridge_unavailable', () => {
    const res = unwrapResult({ message: { success: false, error: 'bridge_unavailable' } });
    expect(isBridgeUnavailable(res)).toBe(true);
    expect(unwrapResult({ message: { success: true, templates: [] } }).success).toBe(true);
    expect(unwrapResult(null).success).toBe(false);
    if (!res.success) expect(errorText(res)).toMatch(/frappe_whatsapp/);
  });
});
