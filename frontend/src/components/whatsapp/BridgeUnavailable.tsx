import { PlugZap } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

export const CONNECTOR_NEEDED = 'Template management and message history need the WhatsApp Business connector app';

export function BridgeUnavailable() {
  return (
    <div role="status" className="flex flex-col items-center gap-2 rounded-sm border border-line p-10 text-center">
      <PlugZap className="h-6 w-6 text-steel" />
      <h3 className="text-sm font-semibold text-ink">WhatsApp Business connector not installed</h3>
      <p className="max-w-md text-xs text-steel">
        {CONNECTOR_NEEDED}. Install it on this site, then reload. Ask your administrator if you are unsure how.
      </p>
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Alert variant="destructive" role="alert">
      <div className="flex items-center justify-between gap-3">
        <AlertDescription>{message}</AlertDescription>
        {onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    </Alert>
  );
}
