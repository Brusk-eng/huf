import type { ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

/** How a section reads and reveals errors; owned by the wizard. */
export interface FieldApi {
  /** Message to show for a field path, or undefined when nothing should show yet. */
  error: (path: string) => string | undefined;
  touch: (path: string) => void;
}

interface FieldProps {
  id: string;
  label: ReactNode;
  error?: string;
  hint?: ReactNode;
  /** e.g. "12/60"; turns red past the limit. */
  counter?: { length: number; max: number };
  labelExtra?: ReactNode;
  children: ReactNode;
}

/** Label, control and one reserved note line (hint or error) so errors never shift the layout. */
export function Field({ id, label, error, hint, counter, labelExtra, children }: FieldProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        {labelExtra}
      </div>
      {children}
      <div className="flex min-h-4 justify-between gap-3 text-[11px]">
        <p id={`${id}-note`} role={error ? 'alert' : undefined} className={error ? 'text-destructive' : 'text-steel'}>
          {error ?? hint}
        </p>
        {counter && (
          <span className={cn('shrink-0', counter.length > counter.max ? 'text-destructive' : 'text-steel')}>
            {counter.length}/{counter.max}
          </span>
        )}
      </div>
    </div>
  );
}

export function StepLayout({ children, aside }: { children: ReactNode; aside: ReactNode }) {
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0 space-y-6">{children}</div>
      <aside aria-label="Preview" className="min-w-0 space-y-4 lg:sticky lg:top-4 lg:self-start">
        {aside}
      </aside>
    </div>
  );
}

export function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="space-y-3 rounded-md border border-line bg-panel p-4">
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {description && <p className="text-xs text-steel">{description}</p>}
      </div>
      {children}
    </section>
  );
}
