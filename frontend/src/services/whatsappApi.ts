import { call } from '@/lib/frappe-sdk';
import { getFrappeErrorMessage } from '@/lib/frappe-error';

const BASE = 'huf.ai.whatsapp_management';

export const TEMPLATE_NAME_RE = /^[a-z0-9_]+$/;
export const TEMPLATE_CATEGORIES = ['MARKETING', 'UTILITY'] as const;
export type TemplateCategory = (typeof TEMPLATE_CATEGORIES)[number];

export interface WhatsAppError {
  success: false;
  error: string;
  message?: string;
  hint?: string;
  expires_at?: string | null;
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

export interface TemplatePayload {
  template_name: string;
  category: TemplateCategory;
  language_code: string;
  template: string;
  header?: string;
  footer?: string;
  sample_values?: string;
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
  };
  return res.message || map[res.error] || res.error;
}

/** Placeholders in order of first appearance, e.g. "Hi {{1}}, {{2}}" -> [1, 2]. */
export function extractPlaceholders(body: string): number[] {
  const seen = new Set<number>();
  for (const m of body.matchAll(/\{\{\s*(\d+)\s*\}\}/g)) seen.add(Number(m[1]));
  return [...seen].sort((a, b) => a - b);
}

export function validateTemplateForm(f: {
  name: string;
  language: string;
  body: string;
  samples: string[];
}): string | null {
  if (!TEMPLATE_NAME_RE.test(f.name)) return 'Name must match ^[a-z0-9_]+$';
  if (!f.language.trim()) return 'Language is required';
  if (!f.body.trim()) return 'Body is required';
  const holes = extractPlaceholders(f.body);
  if (holes.some((n, i) => n !== i + 1)) return 'Placeholders must be numbered {{1}}, {{2}}, ... without gaps';
  if (holes.length > 0 && f.samples.slice(0, holes.length).some((s) => !s.trim())) {
    return 'Provide a sample value for every placeholder';
  }
  return null;
}

export function buildTemplatePayload(f: {
  name: string;
  category: TemplateCategory;
  language: string;
  body: string;
  header?: string;
  footer?: string;
  samples: string[];
}): TemplatePayload {
  const count = extractPlaceholders(f.body).length;
  const payload: TemplatePayload = {
    template_name: f.name.trim(),
    category: f.category,
    language_code: f.language.trim(),
    template: f.body,
  };
  if (f.header?.trim()) payload.header = f.header.trim();
  if (f.footer?.trim()) payload.footer = f.footer.trim();
  if (count > 0) payload.sample_values = f.samples.slice(0, count).map((s) => s.trim()).join(',');
  return payload;
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
  header_params?: string[];
  account?: string;
}) =>
  invoke<Record<string, unknown>>(
    'send_template_message',
    {
      to: args.to,
      template_name: args.template_name,
      body_params: JSON.stringify(args.body_params ?? []),
      header_params: JSON.stringify(args.header_params ?? []),
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
