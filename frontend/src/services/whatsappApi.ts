import { call, file } from '@/lib/frappe-sdk';
import { getFrappeErrorMessage } from '@/lib/frappe-error';

const BASE = 'huf.ai.whatsapp_management';

export const TEMPLATE_NAME_RE = /^[a-z0-9_]+$/;
export const TEMPLATE_CATEGORIES = ['MARKETING', 'UTILITY'] as const;
export type TemplateCategory = (typeof TEMPLATE_CATEGORIES)[number];

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

export interface WhatsAppError {
  success: false;
  error: string;
  message?: string;
  hint?: string;
  expires_at?: string | null;
  /** Present for `validation_failed`: every problem found, per field. */
  errors?: FieldError[];
}
export type WhatsAppResult<T> = ({ success: true } & T) | WhatsAppError;

export interface WhatsAppAccount {
  name: string;
  phone_id?: string;
  status?: string;
}

export interface GatewayStatus {
  name: string;
  enabled: boolean;
  whatsapp_account?: string | null;
  app_secret_present: boolean;
  app_secret_missing: boolean;
  verify_token_present: boolean;
  account_phone_mismatch?: boolean;
  webhook_url: string;
  callback_warning: string;
  failing_checks: string[];
}

export interface SetupStatus {
  bridge_available: boolean;
  accounts: WhatsAppAccount[];
  gateways: GatewayStatus[];
  callback_warning: string;
}

export interface WhatsAppTemplate {
  name: string;
  template_name: string;
  actual_name?: string;
  category?: string;
  language_code?: string;
  status?: string;
  id?: string;
  whatsapp_account?: string;
  template?: string;
  header_type?: HeaderType | '';
  header?: string;
  footer?: string;
  sample_values?: string;
  buttons?: { button_type: string; button_label?: string; url_type?: string; website_url?: string; phone_number?: string }[];
}

export interface WhatsAppMessage {
  name: string;
  type?: 'Incoming' | 'Outgoing' | string;
  to?: string;
  from?: string;
  message?: string;
  message_id?: string;
  content_type?: string;
  status?: string;
  creation?: string;
  whatsapp_account?: string;
  profile_name?: string;
}

export interface WhatsAppConversation {
  phone: string;
  last_message: WhatsAppMessage;
  count: number;
  profile_name?: string | null;
}

export const TEMPLATE_LIMITS = { body: 1024, header: 60, footer: 60, button: 25, quickReplies: 3, cta: 2 } as const;
export const HEADER_TYPES = ['TEXT', 'IMAGE', 'DOCUMENT'] as const;
export type HeaderType = (typeof HEADER_TYPES)[number];
export const MEDIA_KINDS = ['image', 'document', 'audio', 'video'] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

export type TemplateButtonType = 'QUICK_REPLY' | 'URL' | 'PHONE_NUMBER';
export interface TemplateButton {
  type: TemplateButtonType;
  text: string;
  url?: string;
  example?: string;
  phone_number?: string;
}

export interface TemplatePayload {
  template_name: string;
  category: TemplateCategory;
  language_code: string;
  template: string;
  header_type?: HeaderType;
  header?: string;
  /** Header example: a value for a TEXT header variable, or a file URL for IMAGE/DOCUMENT. */
  sample?: string;
  footer?: string;
  sample_values?: string;
  buttons?: TemplateButton[];
}

export type HeaderParam = string | { type: 'image' | 'document' | 'video'; link: string; filename?: string };
export interface ButtonParam {
  index: number;
  sub_type: 'url' | 'quick_reply';
  text: string;
}

/** Unwrap the `{message: ...}` envelope; thrown transport errors become error results. */
async function invoke<T>(
  method: string,
  args: Record<string, unknown>,
  verb: 'get' | 'post',
): Promise<WhatsAppResult<T>> {
  try {
    const res = verb === 'post' ? await call.post(`${BASE}.${method}`, args) : await call.get(`${BASE}.${method}`, args);
    return unwrapResult<T>(res);
  } catch (err) {
    return { success: false, error: 'request_failed', message: getFrappeErrorMessage(err) };
  }
}

export function unwrapResult<T>(res: unknown): WhatsAppResult<T> {
  const body = (res && typeof res === 'object' && 'message' in res ? (res as { message: unknown }).message : res) as
    | (Partial<WhatsAppError> & { success?: boolean })
    | null
    | undefined;
  if (!body || typeof body !== 'object') {
    return { success: false, error: 'invalid_response', message: 'Unexpected response from server.' };
  }
  if (body.success === false || body.error) {
    return { ...(body as object), success: false, error: body.error || 'unknown_error' } as WhatsAppError;
  }
  return body as WhatsAppResult<T>;
}

export function isBridgeUnavailable(res: { success: boolean; error?: string }): boolean {
  return !res.success && res.error === 'bridge_unavailable';
}

export function errorText(res: WhatsAppError): string {
  const map: Record<string, string> = {
    bridge_unavailable: 'The optional frappe_whatsapp app is not installed on this site.',
    no_account: 'No WhatsApp Account could be resolved. Select an account first.',
    window_closed: 'The 24-hour customer-service window is closed for this number.',
    invalid_recipient: 'Enter a phone number with 7 to 15 digits.',
    invalid_name: 'Template name may only contain lowercase letters, digits and underscores.',
    template_in_use: 'This template has been used in sent messages and cannot be deleted.',
  };
  if (res.errors?.length) return res.errors.map((e) => e.message).join(' ');
  return res.message || map[res.error] || res.error;
}

/** Placeholders in order of first appearance, e.g. "Hi {{1}}, {{2}}" -> [1, 2]. */
export function extractPlaceholders(body: string): number[] {
  const seen = new Set<number>();
  for (const m of body.matchAll(/\{\{\s*(\d+)\s*\}\}/g)) seen.add(Number(m[1]));
  return [...seen].sort((a, b) => a - b);
}

export interface TemplateForm {
  name: string;
  category?: TemplateCategory;
  language: string;
  body: string;
  samples: string[];
  headerType?: HeaderType | '';
  header?: string;
  headerSample?: string;
  footer?: string;
  buttons?: TemplateButton[];
}

export function validateTemplateForm(f: TemplateForm): string | null {
  if (!TEMPLATE_NAME_RE.test(f.name)) return 'Name must match ^[a-z0-9_]+$';
  if (!f.language.trim()) return 'Language is required';
  if (!f.body.trim()) return 'Body is required';
  if (f.body.length > TEMPLATE_LIMITS.body) return `Body is limited to ${TEMPLATE_LIMITS.body} characters`;
  const holes = extractPlaceholders(f.body);
  if (holes.some((n, i) => n !== i + 1)) return 'Placeholders must be numbered {{1}}, {{2}}, ... without gaps';
  if (holes.length > 0 && f.samples.slice(0, holes.length).some((s) => !s.trim())) {
    return 'Provide a sample value for every placeholder';
  }
  if (f.samples.slice(0, holes.length).some((s) => s.includes(','))) return 'Sample values cannot contain commas';

  if (f.headerType === 'TEXT') {
    const text = f.header ?? '';
    if (!text.trim()) return 'Header text is required';
    if (text.length > TEMPLATE_LIMITS.header) return `Header text is limited to ${TEMPLATE_LIMITS.header} characters`;
    const headerHoles = extractPlaceholders(text);
    if (headerHoles.length > 1 || (headerHoles.length === 1 && headerHoles[0] !== 1)) {
      return 'A text header allows at most one variable, {{1}}';
    }
    if (headerHoles.length === 1 && !(f.headerSample ?? '').trim()) return 'Provide an example for the header variable';
  } else if (f.headerType === 'IMAGE' || f.headerType === 'DOCUMENT') {
    if (!/^https?:\/\//.test((f.headerSample ?? '').trim())) return 'Provide an http(s) link to a sample file for the header';
  }

  const footer = f.footer ?? '';
  if (footer.length > TEMPLATE_LIMITS.footer) return `Footer is limited to ${TEMPLATE_LIMITS.footer} characters`;
  if (footer.includes('{{')) return 'The footer cannot contain variables';

  const buttons = f.buttons ?? [];
  const quick = buttons.filter((b) => b.type === 'QUICK_REPLY').length;
  if (quick > TEMPLATE_LIMITS.quickReplies) return `At most ${TEMPLATE_LIMITS.quickReplies} quick-reply buttons`;
  if (buttons.length - quick > TEMPLATE_LIMITS.cta) return `At most ${TEMPLATE_LIMITS.cta} link or phone buttons`;
  if (buttons.filter((b) => b.type === 'PHONE_NUMBER').length > 1) return 'Only one phone button is allowed';
  const kinds = buttons.map((b) => b.type === 'QUICK_REPLY');
  if (kinds.filter((k, i) => i > 0 && k !== kinds[i - 1]).length > 1) {
    return 'Keep quick-reply buttons together and link/phone buttons together';
  }
  for (const [i, b] of buttons.entries()) {
    const label = `Button ${i + 1}`;
    if (!b.text.trim()) return `${label} needs text`;
    if (b.text.length > TEMPLATE_LIMITS.button) return `${label} text is limited to ${TEMPLATE_LIMITS.button} characters`;
    if (b.type === 'URL') {
      if (!/^https?:\/\//.test(b.url ?? '')) return `${label} needs an http(s) link`;
      if ((b.url ?? '').includes('{{')) {
        if (!(b.url ?? '').endsWith('{{1}}') || (b.url ?? '').split('{{').length > 2) return `${label}: the link may only end with {{1}}`;
        if (!(b.example ?? '').trim()) return `${label} needs an example link`;
      }
    }
    if (b.type === 'PHONE_NUMBER' && !/^\+?[0-9 ()\-.]{7,20}$/.test(b.phone_number ?? '')) {
      return `${label} needs a phone number such as +15551234567`;
    }
  }
  return null;
}

export function buildTemplatePayload(f: TemplateForm & { category: TemplateCategory }): TemplatePayload {
  const count = extractPlaceholders(f.body).length;
  const payload: TemplatePayload = {
    template_name: f.name.trim(),
    category: f.category,
    language_code: f.language.trim(),
    template: f.body,
  };
  if (f.headerType === 'TEXT' && f.header?.trim()) {
    payload.header_type = 'TEXT';
    payload.header = f.header.trim();
    if (f.headerSample?.trim() && extractPlaceholders(f.header).length > 0) payload.sample = f.headerSample.trim();
  } else if (f.headerType === 'IMAGE' || f.headerType === 'DOCUMENT') {
    payload.header_type = f.headerType;
    payload.sample = (f.headerSample ?? '').trim();
  } else if (!f.headerType && f.header?.trim()) {
    payload.header = f.header.trim(); // legacy: plain header text
  }
  if (f.footer?.trim()) payload.footer = f.footer.trim();
  if (count > 0) payload.sample_values = f.samples.slice(0, count).map((s) => s.trim()).join(',');
  if (f.buttons?.length) {
    payload.buttons = f.buttons.map((b) => {
      const out: TemplateButton = { type: b.type, text: b.text.trim() };
      if (b.type === 'URL') {
        out.url = (b.url ?? '').trim();
        if (out.url.includes('{{') && b.example?.trim()) out.example = b.example.trim();
      }
      if (b.type === 'PHONE_NUMBER') out.phone_number = (b.phone_number ?? '').trim();
      return out;
    });
  }
  return payload;
}

/** Validate an outbound media send before calling the server. */
export function validateMediaSend(f: { kind: MediaKind; link?: string; fileUrl?: string; caption?: string }): string | null {
  if (!f.link?.trim() && !f.fileUrl) return 'Add a file or paste a public link';
  if (f.link?.trim() && !/^https:\/\//.test(f.link.trim())) return 'The link must start with https://';
  if (f.kind === 'audio' && f.caption?.trim()) return 'Audio messages cannot have a caption';
  if ((f.caption ?? '').length > 1024) return 'Caption is limited to 1024 characters';
  return null;
}

export const getSetupStatus = (gatewayName?: string) =>
  invoke<SetupStatus>('get_setup_status', gatewayName ? { gateway_name: gatewayName } : {}, 'get');

export const listTemplates = (account?: string, status?: string) =>
  invoke<{ templates: WhatsAppTemplate[] }>('list_templates', { account, status }, 'get');

export const createTemplate = (account: string | undefined, payload: TemplatePayload) =>
  invoke<{ name: string; status?: string; id?: string }>(
    'create_template',
    { account, payload: JSON.stringify(payload) },
    'post',
  );

export const deleteTemplate = (name: string) => invoke<Record<string, unknown>>('delete_template', { name }, 'post');

export const syncTemplates = (account?: string) =>
  invoke<Record<string, unknown>>('sync_templates', { account }, 'post');

export const sendTemplateMessage = (args: {
  to: string;
  template_name: string;
  body_params?: string[];
  header_params?: HeaderParam[];
  button_params?: ButtonParam[];
  account?: string;
}) =>
  invoke<Record<string, unknown>>(
    'send_template_message',
    {
      to: args.to,
      template_name: args.template_name,
      body_params: JSON.stringify(args.body_params ?? []),
      header_params: JSON.stringify(args.header_params ?? []),
      button_params: JSON.stringify(args.button_params ?? []),
      account: args.account,
    },
    'post',
  );

export const sendTextMessage = (gatewayName: string, to: string, text: string) =>
  invoke<Record<string, unknown>>('send_text_message', { gateway_name: gatewayName, to, text }, 'post');

export const listMessages = (account?: string, phone?: string, limit = 50) =>
  invoke<{ messages: WhatsAppMessage[] }>('list_messages', { account, phone, limit }, 'get');

export const listConversations = (account?: string, limit = 50) =>
  invoke<{ conversations: WhatsAppConversation[] }>('list_conversations', { account, limit }, 'get');

export const sendMediaMessage = (
  gatewayName: string,
  to: string,
  media: { kind: MediaKind; link?: string; fileUrl?: string; caption?: string; filename?: string },
) =>
  invoke<Record<string, unknown>>(
    'send_media_message',
    {
      gateway_name: gatewayName,
      to,
      kind: media.kind,
      link: media.link?.trim() || undefined,
      file_url: media.fileUrl || undefined,
      caption: media.caption?.trim() || undefined,
      filename: media.filename?.trim() || undefined,
    },
    'post',
  );

/** Upload a public file (so Meta can fetch it) and return its server path. */
export async function uploadPublicFile(f: File): Promise<{ fileUrl: string } | WhatsAppError> {
  try {
    const res = await file.uploadFile(f, { isPrivate: false });
    const data = (res as { data?: { message?: { file_url?: string }; file_url?: string } }).data;
    const fileUrl = data?.message?.file_url || data?.file_url;
    if (!fileUrl) return { success: false, error: 'upload_failed', message: 'The upload did not return a file.' };
    return { fileUrl };
  } catch (err) {
    return { success: false, error: 'upload_failed', message: getFrappeErrorMessage(err) };
  }
}
