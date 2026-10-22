import { TimeStamp } from './TimeStamp';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getFrappeErrorMessage } from '@/lib/frappe-error';
import {
  approveGatewayPairing, listGatewayAccessEntries, revokeGatewayAccessEntry,
  type GatewayAccessEntry, type GatewayAccessEntryState,
} from '@/services/gatewayAccessApi';
import { LoadError } from './BridgeUnavailable';

const STATES: GatewayAccessEntryState[] = ['Pending', 'Approved', 'Revoked'];
const STATE_LABEL: Record<GatewayAccessEntryState, string> = { Pending: 'Waiting', Approved: 'Approved', Revoked: 'Revoked' };
const EMPTY: Record<GatewayAccessEntryState, string> = {
  Pending: 'No one is waiting for approval.',
  Approved: 'No approved senders yet.',
  Revoked: 'No revoked senders.',
};

/** Who may talk to this gateway's agent: approve waiting requests or revoke access. */
export function AccessTab({ gateway }: { gateway: string }) {
  const [state, setState] = useState<GatewayAccessEntryState>('Pending');
  const [entries, setEntries] = useState<GatewayAccessEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setEntries(await listGatewayAccessEntries({ gateway, state }));
    } catch (e) {
      setEntries([]);
      setError(getFrappeErrorMessage(e));
    }
    setLoading(false);
  }, [gateway, state]);
  useEffect(() => { void load(); }, [load]);

  const act = async (entry: GatewayAccessEntry, action: 'approve' | 'revoke') => {
    setBusy(entry.name);
    try {
      if (action === 'approve') {
        const res = await approveGatewayPairing(entry.name);
        toast.success('Access approved');
        if (/not sent/i.test(res.notification_status)) toast.message('The welcome message could not be sent.');
      } else {
        await revokeGatewayAccessEntry(entry.name);
        toast.success('Access revoked');
      }
      await load();
    } catch (e) {
      toast.error(getFrappeErrorMessage(e));
    }
    setBusy('');
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-steel">People who message this number must be approved before the agent replies, when pairing is on.</p>
      <Tabs value={state} onValueChange={(v) => setState(v as GatewayAccessEntryState)}>
        <TabsList>{STATES.map((s) => <TabsTrigger key={s} value={s}>{STATE_LABEL[s]}</TabsTrigger>)}</TabsList>
      </Tabs>
      {error && <LoadError message={error} onRetry={load} />}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-steel"><Loader2 className="h-4 w-4 animate-spin" /> Loading...</div>
      ) : entries.length === 0 && !error ? (
        <p className="rounded-sm border border-line p-6 text-center text-xs text-steel">{EMPTY[state]}</p>
      ) : entries.length > 0 ? (
        <div className="rounded-sm border border-line">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contact</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Requested</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e) => (
                <TableRow key={e.name}>
                  <TableCell className="text-xs">
                    <div className="font-mono text-ink">{e.external_id}</div>
                    {e.display_label && <div className="text-steel">{e.display_label}</div>}
                  </TableCell>
                  <TableCell className="text-xs">{e.entry_type === 'Room' ? 'Group' : 'Person'}</TableCell>
                  <TableCell className="font-mono text-xs">{e.pairing_code || ''}</TableCell>
                  <TableCell>
                    <Badge variant={e.state === 'Approved' ? 'pill-success' : e.state === 'Revoked' ? 'pill-danger' : 'pill-warning'}>{STATE_LABEL[e.state]}</Badge>
                    {e.approved_by && <div className="text-[11px] text-steel">by {e.approved_by}</div>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-steel"><TimeStamp value={e.creation} /></TableCell>
                  <TableCell className="space-x-2 whitespace-nowrap text-right">
                    {e.state === 'Pending' && (
                      <Button size="sm" disabled={busy === e.name} onClick={() => void act(e, 'approve')}>Approve</Button>
                    )}
                    {e.state !== 'Revoked' && (
                      <Button size="sm" variant="outline" disabled={busy === e.name} onClick={() => void act(e, 'revoke')}>
                        {e.state === 'Pending' ? 'Decline' : 'Revoke'}
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </div>
  );
}
