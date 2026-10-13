import { describe, it, expect } from 'vitest';
import {
  scheduleFormToArgs,
  docEventFormToArgs,
  summaryToScheduleForm,
  summaryToDocEventForm,
  describeTriggerState,
  triggerErrorInfo,
  triggerDisabledReason,
  isTriggerBoundFor,
  DEFAULT_SCHEDULE_FORM,
  type FlowTriggerSummary,
} from './flowTrigger';

const base: FlowTriggerSummary = {
  flow_id: 'f1',
  trigger_type: 'Schedule',
  automation: 'Flow Trigger: f1',
  automation_trigger: 't1',
  automation_status: 'Active',
  procedure: 'f1-procedure-1',
  procedure_status: 'Testing',
  enabled: true,
  next_run: '2026-01-01 10:00:00',
  last_run: null,
};

describe('scheduleFormToArgs', () => {
  it('maps cron mode and normalises whitespace', () => {
    const r = scheduleFormToArgs('f1', { ...DEFAULT_SCHEDULE_FORM, mode: 'cron', cron: ' 0  */6 * * * ' });
    expect(r).toEqual({ ok: true, args: { flow_id: 'f1', trigger_type: 'schedule', cron: '0 */6 * * *' } });
  });
  it('rejects bad cron', () => {
    expect(scheduleFormToArgs('f1', { ...DEFAULT_SCHEDULE_FORM, mode: 'cron', cron: '* * *' }).ok).toBe(false);
  });
  it('maps interval mode', () => {
    const r = scheduleFormToArgs('f1', { ...DEFAULT_SCHEDULE_FORM, interval: 'Weekly', intervalCount: 2 });
    expect(r).toEqual({
      ok: true,
      args: { flow_id: 'f1', trigger_type: 'schedule', interval: 'Weekly', interval_count: 2 },
    });
  });
  it('rejects non-positive counts', () => {
    expect(scheduleFormToArgs('f1', { ...DEFAULT_SCHEDULE_FORM, intervalCount: 0 }).ok).toBe(false);
  });
});

describe('docEventFormToArgs', () => {
  it('requires a doctype', () => {
    expect(docEventFormToArgs('f1', { referenceDoctype: ' ', docEvent: 'on_update', condition: '' }).ok).toBe(false);
  });
  it('omits empty condition', () => {
    const r = docEventFormToArgs('f1', { referenceDoctype: 'ToDo', docEvent: 'on_update', condition: ' ' });
    expect(r).toEqual({
      ok: true,
      args: { flow_id: 'f1', trigger_type: 'doc_event', reference_doctype: 'ToDo', doc_event: 'on_update' },
    });
  });
});

describe('hydration', () => {
  it('round-trips interval schedules', () => {
    const f = summaryToScheduleForm({ ...base, schedule_type: 'Interval', interval: 'Monthly', interval_count: 3 });
    expect(f).toMatchObject({ mode: 'interval', interval: 'Monthly', intervalCount: 3 });
  });
  it('round-trips cron schedules', () => {
    expect(summaryToScheduleForm({ ...base, schedule_type: 'Cron', cron: '5 4 * * *' })).toMatchObject({
      mode: 'cron',
      cron: '5 4 * * *',
    });
  });
  it('falls back to defaults for other trigger types or none', () => {
    expect(summaryToScheduleForm(null)).toEqual(DEFAULT_SCHEDULE_FORM);
    expect(summaryToDocEventForm({ ...base }).referenceDoctype).toBe('');
  });
  it('hydrates doc events', () => {
    const f = summaryToDocEventForm({
      ...base,
      trigger_type: 'Doc Event',
      reference_doctype: 'ToDo',
      doc_event: 'on_submit',
      condition: 'doc.x',
    });
    expect(f).toEqual({ referenceDoctype: 'ToDo', docEvent: 'on_submit', condition: 'doc.x' });
  });
});

describe('describeTriggerState', () => {
  it('is live only when Active and enabled', () => {
    expect(describeTriggerState(base).kind).toBe('live');
    expect(describeTriggerState({ ...base, enabled: false }).kind).toBe('disabled');
  });
  it('never claims live for a Draft automation', () => {
    const s = describeTriggerState({
      ...base,
      automation_status: 'Draft',
      enabled: false,
      procedure_status: 'Draft',
      activation_required: 'Procedure p is Draft.',
    });
    expect(s).toMatchObject({ kind: 'activation_required', procedure: 'f1-procedure-1', message: 'Procedure p is Draft.' });
  });
  it('handles no trigger', () => {
    expect(describeTriggerState(null).kind).toBe('none');
  });
});

describe('triggerErrorInfo', () => {
  it('flags non-deterministic flows', () => {
    const e = triggerErrorInfo(new Error("Flow 'f1' cannot take a schedule or doc-event trigger: contains agent.run"));
    expect(e.kind).toBe('not_convertible');
    expect(e.message).toContain('contains agent.run');
  });
  it('passes other messages through, stripped of html', () => {
    expect(triggerErrorInfo(new Error('<b>Not permitted</b>'))).toEqual({ kind: 'other', message: 'Not permitted' });
  });
});

describe('triggerDisabledReason', () => {
  it('explains unsaved flows and dirty graphs', () => {
    expect(triggerDisabledReason(null, false)).toMatch(/Save this flow/);
    expect(triggerDisabledReason('f1', true)).toMatch(/Save your changes/);
    expect(triggerDisabledReason('f1', false)).toBeNull();
  });
});

describe('isTriggerBoundFor', () => {
  const s = { trigger_type: 'Schedule' } as FlowTriggerSummary;
  it('is true only when the server trigger matches the node kind', () => {
    expect(isTriggerBoundFor('schedule', s)).toBe(true);
    expect(isTriggerBoundFor('doc-event', s)).toBe(false);
    expect(isTriggerBoundFor('doc-event', { trigger_type: 'Doc Event' } as FlowTriggerSummary)).toBe(true);
    expect(isTriggerBoundFor('schedule', null)).toBe(false);
  });
});
