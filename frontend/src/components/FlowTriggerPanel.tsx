import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Clock } from 'lucide-react';
import { toast } from 'sonner';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Label } from './ui/label';
import { Combobox } from './ui/combobox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { clearFlowTrigger, getFlowTrigger, setFlowTrigger } from '../services/flowApi';
import {
  DOC_EVENTS,
  SCHEDULE_INTERVALS,
  DEFAULT_DOC_EVENT_FORM,
  DEFAULT_SCHEDULE_FORM,
  describeTriggerState,
  docEventFormToArgs,
  scheduleFormToArgs,
  summaryToDocEventForm,
  summaryToScheduleForm,
  triggerDisabledReason,
  triggerErrorInfo,
  type DocEventForm,
  type FlowDocEvent,
  isTriggerBoundFor,
  type FlowTriggerSummary,
  type ScheduleForm,
  type ScheduleInterval,
  type TriggerErrorInfo,
} from '../lib/flowTrigger';

interface FlowTriggerPanelProps {
  kind: 'schedule' | 'doc-event';
  flowId: string | null | undefined;
  hasUnsavedChanges: boolean;
  docTypes: Array<{ value: string; label: string }>;
  loadingDocTypes: boolean;
  /** Reports whether the server holds a trigger of this kind, after a hydrate / save / clear succeeds. */
  onBoundChange?: (bound: boolean) => void;
}

/**
 * Schedule / doc-event trigger editor. State lives in the backend (an Automation bound to the
 * flow's converted Procedure), not in the node config, so it hydrates from get_flow_trigger
 * and writes through set_flow_trigger.
 */
export function FlowTriggerPanel({ kind, flowId, hasUnsavedChanges, docTypes, loadingDocTypes, onBoundChange }: FlowTriggerPanelProps) {
  const navigate = useNavigate();
  const [summary, setSummary] = useState<FlowTriggerSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<TriggerErrorInfo | null>(null);
  const [scheduleForm, setScheduleForm] = useState<ScheduleForm>(DEFAULT_SCHEDULE_FORM);
  const [docForm, setDocForm] = useState<DocEventForm>(DEFAULT_DOC_EVENT_FORM);

  // Latest callback without refetching when its identity changes (it closes over the selected node).
  const boundRef = useRef(onBoundChange);
  boundRef.current = onBoundChange;

  const hydrate = useCallback((s: FlowTriggerSummary | null) => {
    setSummary(s);
    setScheduleForm(summaryToScheduleForm(s));
    setDocForm(summaryToDocEventForm(s));
  }, []);

  useEffect(() => {
    setError(null);
    if (!flowId) {
      hydrate(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getFlowTrigger(flowId)
      .then((s) => {
        if (cancelled) return;
        hydrate(s);
        boundRef.current?.(isTriggerBoundFor(kind, s));
      })
      .catch((e) => !cancelled && setError(triggerErrorInfo(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [flowId, kind, hydrate]);

  const disabledReason = triggerDisabledReason(flowId, hasUnsavedChanges);
  const state = describeTriggerState(summary);
  // A saved trigger of the other kind is replaced on save; say so rather than silently overwrite.
  const otherKind =
    summary && (kind === 'schedule' ? summary.trigger_type === 'Doc Event' : summary.trigger_type === 'Schedule');

  const save = async () => {
    if (!flowId) return;
    const built = kind === 'schedule' ? scheduleFormToArgs(flowId, scheduleForm) : docEventFormToArgs(flowId, docForm);
    if (!built.ok) {
      setError({ kind: 'other', message: built.error });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await setFlowTrigger(built.args);
      hydrate(next);
      boundRef.current?.(isTriggerBoundFor(kind, next));
      toast.success(next.enabled && next.automation_status === 'Active' ? 'Trigger saved' : 'Trigger saved, activation pending');
    } catch (e) {
      setError(triggerErrorInfo(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!flowId) return;
    setBusy(true);
    setError(null);
    try {
      await clearFlowTrigger(flowId);
      hydrate(null);
      boundRef.current?.(false);
      toast.success('Trigger removed');
    } catch (e) {
      setError(triggerErrorInfo(e));
    } finally {
      setBusy(false);
    }
  };

  const openProcedure = (name: string) => navigate(`/procedures/${encodeURIComponent(name)}`);
  const formDisabled = Boolean(disabledReason) || busy || loading;

  return (
    <div className="space-y-3">
      {disabledReason ? <p className="text-xs text-muted-foreground">{disabledReason}</p> : null}

      {kind === 'schedule' ? (
        <>
          <div>
            <Label htmlFor="schedule-mode">Schedule type</Label>
            <Select
              value={scheduleForm.mode}
              disabled={formDisabled}
              onValueChange={(v) => setScheduleForm({ ...scheduleForm, mode: v as ScheduleForm['mode'] })}
            >
              <SelectTrigger id="schedule-mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="interval">Interval</SelectItem>
                <SelectItem value="cron">Cron expression</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {scheduleForm.mode === 'interval' ? (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label htmlFor="interval-count">Every</Label>
                <Input
                  id="interval-count"
                  type="number"
                  min="1"
                  disabled={formDisabled}
                  value={scheduleForm.intervalCount}
                  onChange={(e) => setScheduleForm({ ...scheduleForm, intervalCount: parseInt(e.target.value) || 0 })}
                />
              </div>
              <div>
                <Label htmlFor="interval-unit">Period</Label>
                <Select
                  value={scheduleForm.interval}
                  disabled={formDisabled}
                  onValueChange={(v) => setScheduleForm({ ...scheduleForm, interval: v as ScheduleInterval })}
                >
                  <SelectTrigger id="interval-unit">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SCHEDULE_INTERVALS.map((i) => (
                      <SelectItem key={i} value={i}>
                        {i}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          ) : (
            <div>
              <Label htmlFor="cron">Cron expression</Label>
              <Input
                id="cron"
                disabled={formDisabled}
                value={scheduleForm.cron}
                onChange={(e) => setScheduleForm({ ...scheduleForm, cron: e.target.value })}
                placeholder="0 */6 * * *"
              />
            </div>
          )}
        </>
      ) : (
        <>
          <div>
            <Label htmlFor="doctype">Document type</Label>
            <Combobox
              id="doctype"
              options={docTypes}
              value={docForm.referenceDoctype}
              onValueChange={(v) => setDocForm({ ...docForm, referenceDoctype: v })}
              placeholder={loadingDocTypes ? 'Loading...' : 'Select DocType...'}
              disabled={formDisabled || loadingDocTypes}
              searchPlaceholder="Search DocType..."
              emptyText="No DocType found."
            />
          </div>
          <div>
            <Label htmlFor="doc-event">Event</Label>
            <Select
              value={docForm.docEvent}
              disabled={formDisabled}
              onValueChange={(v) => setDocForm({ ...docForm, docEvent: v as FlowDocEvent })}
            >
              <SelectTrigger id="doc-event">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DOC_EVENTS.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="doc-condition">Condition (optional)</Label>
            <Input
              id="doc-condition"
              disabled={formDisabled}
              value={docForm.condition}
              onChange={(e) => setDocForm({ ...docForm, condition: e.target.value })}
              placeholder="doc.status == 'Open'"
            />
          </div>
        </>
      )}

      {otherKind ? (
        <p className="text-xs text-muted-foreground">
          This flow already has a {summary?.trigger_type} trigger. Saving replaces it.
        </p>
      ) : null}

      {error ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
          {error.message}
        </div>
      ) : null}

      {!otherKind && summary ? (
        <div
          role="status"
          className="rounded-md border border-border bg-paper p-2 text-xs space-y-1.5"
        >
          <p className={state.kind === 'live' ? 'font-medium' : 'text-muted-foreground'}>{state.message}</p>
          {state.kind === 'activation_required' ? (
            <div className="flex flex-wrap items-center gap-2">
              {state.procedure ? (
                <Button size="sm" variant="outline" onClick={() => openProcedure(state.procedure!)}>
                  Open Procedure
                </Button>
              ) : null}
              <Button size="sm" variant="outline" disabled={formDisabled} onClick={save}>
                Retry activation
              </Button>
            </div>
          ) : null}
          {kind === 'schedule' && summary.next_run && state.kind === 'live' ? (
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <Clock className="w-3 h-3 shrink-0" />
              Next run: {summary.next_run} (server time)
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="flex gap-2">
        <Button size="sm" disabled={formDisabled} onClick={save}>
          {summary ? 'Save trigger' : 'Set trigger'}
        </Button>
        {summary ? (
          <Button size="sm" variant="outline" disabled={formDisabled} onClick={clear}>
            Clear
          </Button>
        ) : null}
      </div>
    </div>
  );
}
