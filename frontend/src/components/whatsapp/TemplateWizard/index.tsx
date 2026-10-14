import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  createTemplate,
  createTemplateDraft,
  editTemplate,
  errorText,
  type WhatsAppError,
} from '@/services/whatsappApi';
import { TemplatePreview } from '../TemplatePreview';
import { kindById, type TemplateKind } from '../template/catalogue';
import { emptyDraft, type TemplateWizardDraft, type WizardButton, type WizardHeader } from '../template/draft';
import { toCreatePayload, toEditPayload } from '../template/payload';
import { validateDraft } from '../template/rules';
import { addButton, moveButton, switchVariableType } from './draftActions';
import { mapBackendErrors } from './backendErrors';
import { StepLayout, type FieldApi } from './fields';
import { draftToPreviewModel } from './previewModel';
import { ReviewStep } from './ReviewStep';
import { Stepper, STEP_TITLES } from './Stepper';
import { TypeStep } from './TypeStep';
import { WriteStep } from './WriteStep';

export type WizardResult = { kind: 'submitted' | 'edited' | 'draft'; name: string };

interface CommonProps {
  gatewayName: string;
  /** Admins send straight to WhatsApp; everyone else asks for approval. */
  isAdmin: boolean;
  /** Always ask for approval, even for admins (agent-drafted templates). */
  requestApproval?: boolean;
  onDone: (result: WizardResult) => void;
  onCancel: () => void;
}

interface CreateProps extends CommonProps {
  initial?: undefined;
  docName?: undefined;
  editsLeft?: undefined;
}

interface EditProps extends CommonProps {
  /** The stored template as a draft, from `fromTemplateDetail`. Name and language are locked. */
  initial: TemplateWizardDraft;
  /** Record name of the template being edited (`TemplateDetail.name`). */
  docName: string;
  /** Edits WhatsApp still allows, when known. */
  editsLeft?: number | null;
}

export type TemplateWizardProps = CreateProps | EditProps;

const WRITE = 1;
const REVIEW = 2;

export function TemplateWizard(props: TemplateWizardProps) {
  const { gatewayName, isAdmin, requestApproval, onDone, onCancel, initial, docName, editsLeft } = props;
  const isEdit = initial !== undefined;
  const direct = isAdmin && !requestApproval;

  const [step, setStep] = useState(isEdit ? WRITE : 0);
  const [draft, setDraft] = useState<TemplateWizardDraft>(() => initial ?? emptyDraft());
  const [kindId, setKindId] = useState(() => (initial ? `${initial.category.toLowerCase()}_default` : ''));
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());
  const [attempts, setAttempts] = useState(0);
  const [serverFields, setServerFields] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const regionRef = useRef<HTMLDivElement>(null);
  const firstRender = useRef(true);
  const buttonSeq = useRef(0);

  const errors = useMemo(() => validateDraft(draft), [draft]);
  const valid = Object.keys(errors).length === 0;
  const kind = kindById(kindId);

  // Move focus to the new step so keyboard and screen reader users land on it.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    regionRef.current?.focus();
  }, [step]);

  // After a blocked Next, put focus on the first field that needs attention.
  useEffect(() => {
    if (attempts > 0) regionRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [attempts]);

  const field: FieldApi = useMemo(
    () => ({
      error: (path) => serverFields[path] ?? (attempts > 0 || touched.has(path) ? errors[path] : undefined),
      touch: (path) => setTouched((t) => (t.has(path) ? t : new Set(t).add(path))),
    }),
    [serverFields, attempts, touched, errors],
  );

  const edit = useCallback((fn: (d: TemplateWizardDraft) => TemplateWizardDraft) => {
    setDraft(fn);
    setServerFields({});
    setNotices([]);
  }, []);
  const patch = (p: Partial<TemplateWizardDraft>) => edit((d) => ({ ...d, ...p }));

  const selectKind = (k: TemplateKind) => {
    setKindId(k.id);
    if (k.category && k.category !== draft.category) patch({ category: k.category, ttl: null });
  };

  const next = () => {
    if (step === WRITE && !valid) {
      setAttempts((n) => n + 1);
      return;
    }
    setStep((s) => Math.min(REVIEW, s + 1));
  };

  const submit = async () => {
    if (busy) return;
    if (!valid) {
      setStep(WRITE);
      setAttempts((n) => n + 1);
      return;
    }
    setBusy(true);
    setNotices([]);
    const res =
      isEdit && docName
        ? await editTemplate(docName, toEditPayload(draft))
        : direct
          ? await createTemplate(gatewayName, toCreatePayload(draft))
          : await createTemplateDraft(gatewayName, toCreatePayload(draft));
    setBusy(false);
    if (res.success) {
      const resultKind = isEdit ? 'edited' : direct ? 'submitted' : 'draft';
      toast.success(
        isEdit ? 'Changes sent to WhatsApp for review' : direct ? 'Template sent to WhatsApp for review' : 'Draft saved. An admin will review it.',
      );
      onDone({ kind: resultKind, name: res.name });
      return;
    }
    showFailure(res);
  };

  const showFailure = (res: WhatsAppError) => {
    if (res.errors?.length) {
      const mapped = mapBackendErrors(res.errors, draft);
      setServerFields(mapped.fields);
      setNotices(mapped.general);
      if (Object.keys(mapped.fields).length > 0) setStep(WRITE);
      else if (mapped.general.length === 0) setNotices([errorText(res)]);
    } else {
      setNotices([errorText(res)]);
    }
  };

  const model = useMemo(() => draftToPreviewModel(draft), [draft]);
  const kindLabel = kind ? `${kind.title} · ${kind.subtype}` : draft.category === 'UTILITY' ? 'Utility' : 'Marketing';
  const finalLabel = isEdit ? 'Submit changes to WhatsApp' : direct ? 'Submit to WhatsApp' : 'Request approval';

  const buttonId = () => `new-${(buttonSeq.current += 1)}`;
  const setButton = (i: number, p: Partial<WizardButton>) =>
    edit((d) => ({ ...d, buttons: d.buttons.map((b, idx) => (idx === i ? { ...b, ...p } : b)) }));

  return (
    <TooltipProvider>
      <div className="space-y-6">
        <Stepper current={step} lockedBefore={isEdit ? WRITE : 0} onGo={setStep} />

        <div ref={regionRef} role="region" aria-label={STEP_TITLES[step]} tabIndex={-1} className="space-y-6 outline-none">
          {step > 0 && notices.length > 0 && (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{notices.join(' ')}</AlertDescription>
            </Alert>
          )}

          {step === 0 && <TypeStep kindId={kindId} onSelect={selectKind} />}

          {step === WRITE && (
            <StepLayout aside={<TemplatePreview model={model} />}>
              <WriteStep
                draft={draft}
                field={field}
                lockIdentity={isEdit}
                editsLeft={editsLeft}
                onChange={patch}
                onVariableType={(to) => edit((d) => switchVariableType(d, to))}
                onHeader={(h: Partial<WizardHeader>) => edit((d) => ({ ...d, header: { ...d.header, ...h } }))}
                onSample={(key, value) => edit((d) => ({ ...d, samples: { ...d.samples, [key]: value } }))}
                onAddButton={(type) => {
                  field.touch('buttons');
                  edit((d) => addButton(d, type, buttonId()));
                }}
                onMoveButton={(i, by) => edit((d) => moveButton(d, i, by))}
                onRemoveButton={(i) => edit((d) => ({ ...d, buttons: d.buttons.filter((_, idx) => idx !== i) }))}
                onButton={setButton}
              />
            </StepLayout>
          )}

          {step === REVIEW && <ReviewStep draft={draft} kindLabel={kindLabel} isEdit={isEdit} direct={direct} />}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
          <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
          <div className="flex items-center gap-2">
            {step > (isEdit ? WRITE : 0) && (
              <Button type="button" variant="outline" disabled={busy} onClick={() => setStep(step - 1)}>Previous</Button>
            )}
            {step < REVIEW ? (
              <Button
                type="button"
                disabled={step === 0 && !kind}
                aria-disabled={step === WRITE && !valid ? true : undefined}
                className={step === WRITE && !valid ? 'opacity-50' : undefined}
                onClick={next}
              >
                Next
              </Button>
            ) : (
              <Button type="button" disabled={busy} onClick={() => void submit()}>
                {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />}
                {finalLabel}
              </Button>
            )}
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
