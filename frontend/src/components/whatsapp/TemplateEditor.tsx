import { useMemo } from 'react';
import type { TemplateDetail } from '@/services/whatsappApi';
import { fromTemplateDetail } from './template/payload';
import { TemplateWizard } from './TemplateWizard';

export type EditorMode = 'create' | 'edit';

interface Props {
  mode: EditorMode;
  gateway?: string;
  /** Required in edit mode: the template being edited. */
  detail?: TemplateDetail;
  isAdmin: boolean;
  onCancel: () => void;
  /** Called with the name of the template (or draft) that was saved. */
  onSaved: (result: { kind: 'submitted' | 'edited' | 'draft'; name: string }) => void;
}

/** Create or edit a template; delegates to the three-step wizard. */
export function TemplateEditor({ mode, gateway, detail, isAdmin, onCancel, onSaved }: Props) {
  const initial = useMemo(() => (mode === 'edit' && detail ? fromTemplateDetail(detail) : undefined), [mode, detail]);
  const common = { gatewayName: gateway ?? '', isAdmin, onDone: onSaved, onCancel };
  if (initial && detail) {
    return <TemplateWizard key={detail.name} {...common} initial={initial} docName={detail.name} />;
  }
  return <TemplateWizard {...common} />;
}
