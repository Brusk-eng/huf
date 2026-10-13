/**
 * Pure helpers mapping the Flow builder's trigger forms onto huf.ai.flow_api
 * set_flow_trigger / get_flow_trigger, and API state onto user-facing messages.
 *
 * Flow triggers are an Automation bound to the Flow's converted Agent Procedure, so
 * the form speaks the backend's vocabulary (5-part cron, or Hourly..Yearly + count;
 * Frappe doc-event method names) rather than the old node-config intervalType/cron fields.
 */

export const SCHEDULE_INTERVALS = ['Hourly', 'Daily', 'Weekly', 'Monthly', 'Yearly'] as const;
export type ScheduleInterval = (typeof SCHEDULE_INTERVALS)[number];

/** Mirrors huf.ai.automation_hooks.SUPPORTED_DOC_EVENTS. */
export const DOC_EVENTS = [
  'before_insert',
  'after_insert',
  'validate',
  'before_save',
  'after_save',
  'before_submit',
  'on_submit',
  'on_update',
  'on_cancel',
  'before_rename',
  'after_rename',
  'on_trash',
  'after_delete',
] as const;
export type FlowDocEvent = (typeof DOC_EVENTS)[number];

/** Node-config key marking that the server holds this node's trigger (see FlowTriggerPanel / flowSerializer). */
export const TRIGGER_BOUND_KEY = 'trigger_bound';

/** True when the server trigger state is a trigger of this node's kind (the node then counts as configured). */
export function isTriggerBoundFor(kind: 'schedule' | 'doc-event', s: FlowTriggerSummary | null): boolean {
  if (!s) return false;
  return s.trigger_type === (kind === 'schedule' ? 'Schedule' : 'Doc Event');
}

/** Shape returned by get_flow_trigger / set_flow_trigger (null when the flow has no trigger). */
export interface FlowTriggerSummary {
  flow_id: string;
  trigger_type: 'Schedule' | 'Doc Event';
  automation: string;
  automation_trigger: string;
  automation_status: string;
  procedure: string;
  procedure_status: string | null;
  enabled: boolean;
  next_run: string | null;
  last_run: string | null;
  activation_required?: string;
  schedule_type?: 'Cron' | 'Interval' | null;
  cron?: string | null;
  interval?: ScheduleInterval | null;
  interval_count?: number | null;
  reference_doctype?: string | null;
  doc_event?: string | null;
  condition?: string | null;
}

export type SetFlowTriggerArgs =
  | { flow_id: string; trigger_type: 'schedule'; cron: string }
  | { flow_id: string; trigger_type: 'schedule'; interval: ScheduleInterval; interval_count: number }
  | {
      flow_id: string;
      trigger_type: 'doc_event';
      reference_doctype: string;
      doc_event: FlowDocEvent;
      condition?: string;
    };

export interface ScheduleForm {
  mode: 'interval' | 'cron';
  interval: ScheduleInterval;
  intervalCount: number;
  cron: string;
}

export interface DocEventForm {
  referenceDoctype: string;
  docEvent: FlowDocEvent;
  condition: string;
}

export const DEFAULT_SCHEDULE_FORM: ScheduleForm = {
  mode: 'interval',
  interval: 'Daily',
  intervalCount: 1,
  cron: '',
};

export const DEFAULT_DOC_EVENT_FORM: DocEventForm = {
  referenceDoctype: '',
  docEvent: 'after_save',
  condition: '',
};

export type ArgsResult<T> = { ok: true; args: T } | { ok: false; error: string };

export function scheduleFormToArgs(flowId: string, form: ScheduleForm): ArgsResult<SetFlowTriggerArgs> {
  if (form.mode === 'cron') {
    const cron = form.cron.trim().replace(/\s+/g, ' ');
    if (cron.split(' ').length !== 5 || !cron) {
      return { ok: false, error: 'Cron needs 5 parts: minute hour day month day-of-week.' };
    }
    return { ok: true, args: { flow_id: flowId, trigger_type: 'schedule', cron } };
  }
  const count = Math.floor(Number(form.intervalCount));
  if (!Number.isFinite(count) || count < 1) {
    return { ok: false, error: 'Interval count must be 1 or more.' };
  }
  return {
    ok: true,
    args: { flow_id: flowId, trigger_type: 'schedule', interval: form.interval, interval_count: count },
  };
}

export function docEventFormToArgs(flowId: string, form: DocEventForm): ArgsResult<SetFlowTriggerArgs> {
  if (!form.referenceDoctype.trim()) return { ok: false, error: 'Choose a document type.' };
  const condition = form.condition.trim();
  return {
    ok: true,
    args: {
      flow_id: flowId,
      trigger_type: 'doc_event',
      reference_doctype: form.referenceDoctype.trim(),
      doc_event: form.docEvent,
      ...(condition ? { condition } : {}),
    },
  };
}

export function summaryToScheduleForm(s: FlowTriggerSummary | null): ScheduleForm {
  if (!s || s.trigger_type !== 'Schedule') return { ...DEFAULT_SCHEDULE_FORM };
  if (s.schedule_type === 'Cron') {
    return { ...DEFAULT_SCHEDULE_FORM, mode: 'cron', cron: s.cron ?? '' };
  }
  return {
    ...DEFAULT_SCHEDULE_FORM,
    mode: 'interval',
    interval: s.interval ?? DEFAULT_SCHEDULE_FORM.interval,
    intervalCount: s.interval_count ?? 1,
  };
}

export function summaryToDocEventForm(s: FlowTriggerSummary | null): DocEventForm {
  if (!s || s.trigger_type !== 'Doc Event') return { ...DEFAULT_DOC_EVENT_FORM };
  const event = DOC_EVENTS.find((e) => e === s.doc_event) ?? DEFAULT_DOC_EVENT_FORM.docEvent;
  return {
    referenceDoctype: s.reference_doctype ?? '',
    docEvent: event,
    condition: s.condition ?? '',
  };
}

export type TriggerStateKind = 'none' | 'live' | 'disabled' | 'activation_required';

export interface TriggerState {
  kind: TriggerStateKind;
  message: string;
  /** Procedure to link to when the user has to act on it. */
  procedure?: string;
}

/** Only `live` may be shown as firing: the Automation must be Active and the trigger enabled. */
export function describeTriggerState(s: FlowTriggerSummary | null): TriggerState {
  if (!s) return { kind: 'none', message: 'No trigger configured for this flow.' };
  if (s.automation_status !== 'Active') {
    return {
      kind: 'activation_required',
      procedure: s.procedure,
      message:
        s.activation_required ||
        `Procedure ${s.procedure} is ${s.procedure_status ?? 'not enabled'}. Set it to Testing or Active, then retry activation.`,
    };
  }
  if (!s.enabled) return { kind: 'disabled', message: 'Trigger is saved but disabled.', procedure: s.procedure };
  return { kind: 'live', message: 'Live: this trigger will fire.', procedure: s.procedure };
}

export interface TriggerErrorInfo {
  kind: 'not_convertible' | 'other';
  message: string;
}

const NOT_CONVERTIBLE_RE = /cannot take a schedule or doc-event trigger:?\s*(.*)$/is;

/** Turn a thrown API error into an inline message; non-deterministic flows are called out. */
export function triggerErrorInfo(err: unknown): TriggerErrorInfo {
  const raw = (err instanceof Error ? err.message : typeof err === 'string' ? err : '') || '';
  const text = raw.replace(/<[^>]*>/g, '').trim();
  const m = NOT_CONVERTIBLE_RE.exec(text);
  if (m || /Flow Is Not Convertible/i.test(text)) {
    const reason = (m?.[1] ?? '').trim();
    return {
      kind: 'not_convertible',
      message:
        'This flow cannot take a schedule or doc-event trigger because it is not deterministic' +
        (reason ? `: ${reason}` : '.') +
        ' Remove agent, LLM-router and approval steps to use it.',
    };
  }
  return { kind: 'other', message: text || 'Could not update the trigger.' };
}

/** Why the form is read-only, or null when it can be edited. */
export function triggerDisabledReason(flowId: string | null | undefined, hasUnsavedChanges: boolean): string | null {
  if (!flowId) return 'Save this flow first. A trigger can only be set on a saved flow.';
  if (hasUnsavedChanges) {
    return 'Save your changes first. The trigger runs the saved version of this flow.';
  }
  return null;
}
