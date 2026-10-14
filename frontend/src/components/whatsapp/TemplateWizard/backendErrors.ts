import type { FieldError } from '@/services/whatsappApi';
import { headerSampleKey, type TemplateWizardDraft } from '../template/draft';
import { extractVariables } from '../template/variables';

const DIRECT: Record<string, string> = {
  template_name: 'name',
  language_code: 'language',
  template: 'body',
  header: 'header.text',
  footer: 'footer',
  ttl_seconds: 'ttl',
};

const BUTTON_FIELD: Record<string, (type: string) => string> = {
  text: () => 'text',
  url: () => 'url',
  phone_number: () => 'phoneNumber',
  example: (type) => (type === 'URL' ? 'urlExample' : 'codeExample'),
};

export interface MappedErrors {
  /** Form field path -> message, ready to show next to the field. */
  fields: Record<string, string>;
  /** Messages that do not belong to a field the form shows. */
  general: string[];
}

function pathFor(field: string, draft: TemplateWizardDraft): string | null {
  if (DIRECT[field]) return DIRECT[field];
  if (field === 'sample') {
    if (draft.header.type !== 'text') return 'header.mediaSample';
    const [token] = extractVariables(draft.header.text, draft.variableType);
    return token ? `samples.${headerSampleKey(token)}` : 'header.text';
  }
  if (field === 'sample_values' || field === 'named_samples') {
    const [token] = extractVariables(draft.body, draft.variableType);
    return token ? `samples.${token}` : 'body';
  }
  if (field.startsWith('named_samples.')) return `samples.${field.slice('named_samples.'.length)}`;
  const button = /^buttons\.(\d+)\.(\w+)$/.exec(field);
  if (button) {
    const i = Number(button[1]);
    const type = draft.buttons[i]?.type ?? '';
    const mapped = BUTTON_FIELD[button[2]]?.(type);
    return mapped ? `buttons.${i}.${mapped}` : null;
  }
  return field === 'buttons' ? 'buttons' : null;
}

/** Map the backend's `errors[]` (field, code, message) back onto the wizard's field paths. */
export function mapBackendErrors(errors: readonly FieldError[], draft: TemplateWizardDraft): MappedErrors {
  const out: MappedErrors = { fields: {}, general: [] };
  for (const e of errors) {
    const path = pathFor(e.field, draft);
    if (!path) out.general.push(e.message);
    else if (!out.fields[path]) out.fields[path] = e.message;
  }
  return out;
}
