import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Plus, RefreshCw, Send, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import {
  TEMPLATE_CATEGORIES, buildTemplatePayload, createTemplate, deleteTemplate, errorText, extractPlaceholders,
  isBridgeUnavailable, listTemplates, sendTemplateMessage, syncTemplates, validateTemplateForm,
  type TemplateCategory, type WhatsAppTemplate,
} from '@/services/whatsappApi';
import { BridgeUnavailable, LoadError } from './BridgeUnavailable';

function statusClass(status?: string) {
  const s = (status || '').toUpperCase();
  if (s === 'APPROVED') return 'text-good';
  if (s === 'REJECTED' || s === 'DISABLED') return 'text-destructive';
  return 'text-signal-ink';
}

export function TemplatesTab({ account }: { account?: string }) {
  const [rows, setRows] = useState<WhatsAppTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<WhatsAppTemplate | null>(null);
  const [toSend, setToSend] = useState<WhatsAppTemplate | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const res = await listTemplates(account);
    if (res.success) {
      setRows(res.templates);
      setUnavailable(false);
    } else if (isBridgeUnavailable(res)) {
      setUnavailable(true);
    } else {
      setError(errorText(res));
    }
    setLoading(false);
  }, [account]);

  useEffect(() => {
    void load();
  }, [load]);

  const sync = async () => {
    setSyncing(true);
    const res = await syncTemplates(account);
    setSyncing(false);
    if (res.success) {
      toast.success('Templates synced');
      void load();
    } else if (isBridgeUnavailable(res)) setUnavailable(true);
    else toast.error(errorText(res));
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    const res = await deleteTemplate(toDelete.name);
    setToDelete(null);
    if (res.success) {
      toast.success('Template deleted');
      void load();
    } else toast.error(errorText(res));
  };

  if (unavailable) return <BridgeUnavailable />;

  return (
    <div className="space-y-3">
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={sync} disabled={syncing || !account}>
          {syncing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />}
          Sync
        </Button>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="mr-1.5 h-4 w-4" /> Create template
        </Button>
      </div>
      {!account && <p className="text-xs text-steel">Select an account above to sync or create templates.</p>}
      {error && <LoadError message={error} onRetry={load} />}
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-steel"><Loader2 className="h-4 w-4 animate-spin" /> Loading templates...</div>
      ) : rows.length === 0 && !error ? (
        <p className="rounded-sm border border-line p-6 text-center text-xs text-steel">No templates yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead><TableHead>Category</TableHead><TableHead>Language</TableHead>
              <TableHead>Status</TableHead><TableHead>Account</TableHead><TableHead className="w-24" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((t) => (
              <TableRow key={t.name}>
                <TableCell className="font-mono text-xs">{t.template_name || t.name}</TableCell>
                <TableCell className="text-xs">{t.category}</TableCell>
                <TableCell className="font-mono text-xs">{t.language_code}</TableCell>
                <TableCell className={`font-mono text-xs uppercase ${statusClass(t.status)}`}>{t.status || 'unknown'}</TableCell>
                <TableCell className="text-xs">{t.whatsapp_account}</TableCell>
                <TableCell className="flex justify-end gap-1">
                  <Button variant="ghost" size="icon" aria-label={`Send ${t.template_name}`} onClick={() => setToSend(t)}>
                    <Send className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" aria-label={`Delete ${t.template_name}`} onClick={() => setToDelete(t)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateDialog open={createOpen} onOpenChange={setCreateOpen} account={account} onCreated={load} />
      <SendDialog template={toSend} onClose={() => setToSend(null)} account={account} />

      <AlertDialog open={!!toDelete} onOpenChange={(o) => !o && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete template?</AlertDialogTitle>
            <AlertDialogDescription>
              {toDelete?.template_name} will be removed. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function CreateDialog(props: { open: boolean; onOpenChange: (o: boolean) => void; account?: string; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [category, setCategory] = useState<TemplateCategory>('UTILITY');
  const [language, setLanguage] = useState('en_US');
  const [body, setBody] = useState('');
  const [header, setHeader] = useState('');
  const [footer, setFooter] = useState('');
  const [samples, setSamples] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState('');
  const holes = extractPlaceholders(body);

  const submit = async () => {
    const problem = validateTemplateForm({ name, language, body, samples });
    if (problem) return setFormError(problem);
    setFormError('');
    setBusy(true);
    const res = await createTemplate(props.account, buildTemplatePayload({ name, category, language, body, header, footer, samples }));
    setBusy(false);
    if (res.success) {
      toast.success('Template submitted');
      props.onOpenChange(false);
      setName(''); setBody(''); setHeader(''); setFooter(''); setSamples([]);
      props.onCreated();
    } else setFormError(errorText(res));
  };

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create template</DialogTitle>
          <DialogDescription>Submitted to Meta for review once created.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="wa-tpl-name">Name</Label>
            <Input id="wa-tpl-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="order_update" className="font-mono" />
            <p className="text-[11px] text-steel">Lowercase letters, digits and underscores.</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Category</Label>
              <Select value={category} onValueChange={(v) => setCategory(v as TemplateCategory)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{TEMPLATE_CATEGORIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="wa-tpl-lang">Language</Label>
              <Input id="wa-tpl-lang" value={language} onChange={(e) => setLanguage(e.target.value)} className="font-mono" />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="wa-tpl-header">Header (optional)</Label>
            <Input id="wa-tpl-header" value={header} onChange={(e) => setHeader(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wa-tpl-body">Body</Label>
            <Textarea id="wa-tpl-body" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Hi {{1}}, your order {{2}} shipped." />
          </div>
          {holes.map((n, i) => (
            <div key={n} className="space-y-1">
              <Label htmlFor={`wa-tpl-sample-${n}`}>Sample for {`{{${n}}}`}</Label>
              <Input
                id={`wa-tpl-sample-${n}`}
                value={samples[i] ?? ''}
                onChange={(e) => setSamples((s) => { const next = [...s]; next[i] = e.target.value; return next; })}
              />
            </div>
          ))}
          <div className="space-y-1">
            <Label htmlFor="wa-tpl-footer">Footer (optional)</Label>
            <Input id="wa-tpl-footer" value={footer} onChange={(e) => setFooter(e.target.value)} />
          </div>
          {formError && <p role="alert" className="text-xs text-destructive">{formError}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => props.onOpenChange(false)}>Cancel</Button>
          <Button onClick={submit} disabled={busy}>{busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SendDialog({ template, onClose, account }: { template: WhatsAppTemplate | null; onClose: () => void; account?: string }) {
  const [to, setTo] = useState('');
  const [params, setParams] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const holes = extractPlaceholders(template?.template || '');

  const send = async () => {
    if (!template) return;
    setBusy(true);
    setErr('');
    const res = await sendTemplateMessage({
      to,
      template_name: template.template_name || template.name,
      body_params: holes.map((_, i) => params[i] ?? ''),
      account: account || template.whatsapp_account,
    });
    setBusy(false);
    if (res.success) {
      toast.success('Template sent');
      setTo(''); setParams([]);
      onClose();
    } else setErr(errorText(res));
  };

  return (
    <Dialog open={!!template} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Send template</DialogTitle>
          <DialogDescription className="font-mono">{template?.template_name}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="wa-send-to">Recipient</Label>
            <Input id="wa-send-to" value={to} onChange={(e) => setTo(e.target.value)} placeholder="+15551234567" className="font-mono" />
          </div>
          {holes.map((n, i) => (
            <div key={n} className="space-y-1">
              <Label htmlFor={`wa-send-p-${n}`}>{`{{${n}}}`}</Label>
              <Input id={`wa-send-p-${n}`} value={params[i] ?? ''} onChange={(e) => setParams((p) => { const next = [...p]; next[i] = e.target.value; return next; })} />
            </div>
          ))}
          {err && <p role="alert" className="text-xs text-destructive">{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={send} disabled={busy || !to.trim()}>{busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}Send</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
