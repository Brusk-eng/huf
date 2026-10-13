import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { PageFrame } from '@/layouts/PageFrame';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { errorText, getSetupStatus, isBridgeUnavailable, type SetupStatus } from '@/services/whatsappApi';
import { BridgeUnavailable, LoadError } from '@/components/whatsapp/BridgeUnavailable';
import { SetupTab } from '@/components/whatsapp/SetupTab';
import { TemplatesTab } from '@/components/whatsapp/TemplatesTab';
import { MessagesTab } from '@/components/whatsapp/MessagesTab';

const ALL = '__all__';

export default function WhatsAppManagePage() {
  const [params] = useSearchParams();
  const gatewayParam = params.get('gateway') || undefined;
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const [account, setAccount] = useState<string>(ALL);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const res = await getSetupStatus();
    if (res.success) {
      setStatus(res);
      setUnavailable(!res.bridge_available);
    } else if (isBridgeUnavailable(res)) setUnavailable(true);
    else setError(errorText(res));
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const gateway = (status?.gateways.find((g) => g.name === gatewayParam) ?? status?.gateways[0])?.name;
  const accountArg = account === ALL ? undefined : account;

  return (
    <PageFrame
      title="Manage WhatsApp"
      actions={
        status && status.accounts.length > 0 ? (
          <Select value={account} onValueChange={setAccount}>
            <SelectTrigger className="w-48" aria-label="WhatsApp account"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All accounts</SelectItem>
              {status.accounts.map((a) => <SelectItem key={a.name} value={a.name}>{a.name}</SelectItem>)}
            </SelectContent>
          </Select>
        ) : undefined
      }
    >
      <div className="space-y-4 p-4">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-steel"><Loader2 className="h-4 w-4 animate-spin" /> Loading...</div>
        ) : error ? (
          <LoadError message={error} onRetry={load} />
        ) : (
          <Tabs defaultValue="setup">
            <TabsList>
              <TabsTrigger value="setup">Setup</TabsTrigger>
              <TabsTrigger value="templates">Templates</TabsTrigger>
              <TabsTrigger value="messages">Messages</TabsTrigger>
            </TabsList>
            <TabsContent value="setup" className="pt-4">
              {status ? <SetupTab status={status} /> : null}
              {unavailable && <div className="mt-6"><BridgeUnavailable /></div>}
            </TabsContent>
            <TabsContent value="templates" className="pt-4">
              {unavailable ? <BridgeUnavailable /> : <TemplatesTab account={accountArg} />}
            </TabsContent>
            <TabsContent value="messages" className="pt-4">
              {unavailable ? <BridgeUnavailable /> : <MessagesTab account={accountArg} gateway={gateway} />}
            </TabsContent>
          </Tabs>
        )}
      </div>
    </PageFrame>
  );
}
