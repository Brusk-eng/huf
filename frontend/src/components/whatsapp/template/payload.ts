import type {
  TemplateDetail,
  WizardEditPayload,
  WizardPayloadButton,
  WizardPayloadHeaderType,
  WizardTemplatePayload,
} from '@/services/whatsappApi';
import {
  emptyButton,
  emptyDraft,
  headerSampleKey,
  type TemplateCategoryId,
  type TemplateWizardDraft,
  type WizardButton,
  type WizardButtonType,
  type WizardHeaderType,
} from './draft';
import { extractVariables, findVariableTokens, type VariableType } from './variables';

const HEADER_TO_API: Record<Exclude<WizardHeaderType, 'none'>, WizardPayloadHeaderType> = {
  text: 'TEXT',
  image: 'IMAGE',
  video: 'VIDEO',
  document: 'DOCUMENT',
  location: 'LOCATION',
};

const BUTTON_TYPES: readonly WizardButtonType[] = ['QUICK_REPLY', 'URL', 'PHONE_NUMBER', 'COPY_CODE'];

function buttonToApi(b: WizardButton): WizardPayloadButton {
  const text = b.text.trim();
  switch (b.type) {
    case 'URL': {
      const url = b.url.trim();
      const out: WizardPayloadButton = { type: 'URL', text, url };
      if (url.includes('{{')) out.example = b.urlExample.trim();
      return out;
    }
    case 'PHONE_NUMBER':
      return { type: 'PHONE_NUMBER', text, phone_number: b.phoneNumber.replace(/[\s\-().]/g, '') };
    case 'COPY_CODE':
      return { type: 'COPY_CODE', text, example: b.codeExample.trim() };
    default:
      return { type: 'QUICK_REPLY', text };
  }
}

/** Create / draft payload for a (validated) wizard draft. */
export function toCreatePayload(draft: TemplateWizardDraft): WizardTemplatePayload {
  const payload: WizardTemplatePayload = {
    template_name: draft.name,
    category: draft.category,
    language_code: draft.language,
    template: draft.body,
    parameter_format: draft.variableType === 'name' ? 'NAMED' : 'POSITIONAL',
  };
  const bodyVars = extractVariables(draft.body, draft.variableType);
  if (bodyVars.length) {
    if (draft.variableType === 'name') {
      payload.named_samples = Object.fromEntries(bodyVars.map((v) => [v, (draft.samples[v] ?? '').trim()]));
    } else {
      payload.sample_values = bodyVars.map((v) => (draft.samples[v] ?? '').trim()).join(',');
    }
  }
  const { header } = draft;
  if (header.type !== 'none') {
    payload.header_type = HEADER_TO_API[header.type];
    if (header.type === 'text') {
      payload.header = header.text;
      const [token] = extractVariables(header.text, draft.variableType);
      if (token) payload.sample = (draft.samples[headerSampleKey(token)] ?? '').trim();
    } else if (header.type !== 'location') {
      payload.sample = header.mediaSample.trim();
    }
  }
  if (draft.footer.trim()) payload.footer = draft.footer.trim();
  if (draft.buttons.length) payload.buttons = draft.buttons.map(buttonToApi);
  if (draft.category === 'UTILITY' && draft.ttl !== null) payload.ttl_seconds = draft.ttl;
  return payload;
}

/** Edit payload: name and language are fixed on an existing template, and cleared parts are sent explicitly. */
export function toEditPayload(draft: TemplateWizardDraft): WizardEditPayload {
  const rest: Partial<WizardTemplatePayload> = toCreatePayload(draft);
  delete rest.template_name;
  delete rest.language_code;
  return {
    ...(rest as WizardEditPayload),
    header_type: rest.header_type ?? '',
    footer: rest.footer ?? '',
    buttons: rest.buttons ?? [],
  };
}

/** Prefill the wizard from a stored template (edit mode). */
export function fromTemplateDetail(detail: TemplateDetail): TemplateWizardDraft {
  const category: TemplateCategoryId = detail.category?.toUpperCase() === 'UTILITY' ? 'UTILITY' : 'MARKETING';
  const draft = emptyDraft(category);
  const { header, body, footer, buttons } = detail.components;
  const bodyTokens = findVariableTokens(body.text);
  const headerTokens = header?.text ? findVariableTokens(header.text) : [];
  const named = [...bodyTokens, ...headerTokens].some((t) => t.kind === 'name');
  const variableType: VariableType = detail.parameter_format === 'NAMED' || named ? 'name' : 'number';

  const samples: Record<string, string> = {};
  extractVariables(body.text, variableType).forEach((token, i) => {
    samples[token] = body.sample_values[i] ?? '';
  });

  const headerType = String(header?.type ?? '').toLowerCase();
  const wizardHeader: TemplateWizardDraft['header'] = { type: 'none', text: '', mediaSample: '' };
  if (headerType === 'text') {
    wizardHeader.type = 'text';
    wizardHeader.text = header?.text ?? '';
    const [token] = extractVariables(wizardHeader.text, variableType);
    if (token) samples[headerSampleKey(token)] = header?.sample ?? '';
  } else if (['image', 'video', 'document', 'location'].includes(headerType)) {
    wizardHeader.type = headerType as WizardHeaderType;
    wizardHeader.mediaSample = header?.sample ?? '';
  }

  const wizardButtons = buttons.flatMap((b, i): WizardButton[] => {
    const type = String(b.type).toUpperCase() as WizardButtonType;
    if (!BUTTON_TYPES.includes(type)) return [];
    const out = emptyButton(type, `button-${i + 1}`);
    out.text = b.text;
    out.url = b.url ?? '';
    out.phoneNumber = b.phone_number ?? '';
    if (type === 'URL') out.urlExample = b.example ?? '';
    if (type === 'COPY_CODE') out.codeExample = b.example ?? '';
    return [out];
  });

  return {
    ...draft,
    name: detail.template_name,
    language: detail.language_code,
    variableType,
    header: wizardHeader,
    body: body.text,
    footer: footer?.text ?? '',
    buttons: wizardButtons,
    ttl: category === 'UTILITY' ? (detail.ttl_seconds ?? null) : null,
    samples,
  };
}
