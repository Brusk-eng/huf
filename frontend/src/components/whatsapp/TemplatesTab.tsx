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
  HEADER_TYPES, TEMPLATE_CATEGORIES, TEMPLATE_LIMITS, buildTemplatePayload, createTemplate, deleteTemplate, errorText,
  extractPlaceholders, isBridgeUnavailable, listTemplates, sendTemplateMessage, syncTemplates, validateTemplateForm,
  type ButtonParam, type HeaderParam, type HeaderType, type TemplateButton, type TemplateButtonType,
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
      toast.success(`Templates synced: ${res.imported ?? 0} imported, ${res.updated ?? 0} updated`);
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
  const [headerType, setHeaderType] = useState<HeaderType | ''>('');
  const [header, setHeader] = useState('');
  const [headerSample, setHeaderSample] = useState('');
  const [footer, setFooter] = useState('');
  const [buttons, setButtons] = useState<TemplateButton[]>([]);
  const [samples, setSamples] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState('');
  const holes = extractPlaceholders(body);
  const headerHasVariable = headerType === 'TEXT' && extractPlaceholders(header).length > 0;
  const quickCount = buttons.filter((b) => b.type === 'QUICK_REPLY').length;
  const linkCount = buttons.length - quickCount;

  const form = { name, category, language, body, samples, headerType, header, headerSample, footer, buttons };

  const submit = async () => {
    const problem = validateTemplateForm(form);
    if (problem) return setFormError(problem);
    setFormError('');
    setBusy(true);
    const res = await createTemplate(props.account, buildTemplatePayload(form));
    setBusy(false);
    if (res.success) {
      toast.success('Template submitted');
      props.onOpenChange(false);
      setName(''); setBody(''); setHeaderType(''); setHeader(''); setHeaderSample(''); setFooter(''); setButtons([]); setSamples([]);
      props.onCreated();
    } else setFormError(errorText(res));
  };

  const updateButton = (i: number, patch: Partial<TemplateButton>) =>
    setButtons((list) => list.map((b, idx) => (idx === i ? { ...b, ...patch } : b)));
  const addButton = (type: TemplateButtonType) => setButtons((list) => [...list, { type, text: '' }]);

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
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
            <Label>Header (optional)</Label>
            <Select value={headerType || 'none'} onValueChange={(v) => setHeaderType(v === 'none' ? '' : (v as HeaderType))}>
              <SelectTrigger aria-label="Header type"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No header</SelectItem>
                {HEADER_TYPES.map((h) => <SelectItem key={h} value={h}>{h === 'TEXT' ? 'Text' : h === 'IMAGE' ? 'Image' : 'Document'}</SelectItem>)}
              </SelectContent>
            </Select>
            {headerType === 'TEXT' && (
              <>
                <Input id="wa-tpl-header" aria-label="Header text" value={header} onChange={(e) => setHeader(e.target.value)} placeholder="Order {{1}}" />
                <p className="text-[11px] text-steel">{header.length}/{TEMPLATE_LIMITS.header} characters. One variable, {'{{1}}'}, is allowed.</p>
              </>
            )}
            {headerHasVariable && (
              <Input aria-label="Header example" value={headerSample} onChange={(e) => setHeaderSample(e.target.value)} placeholder="Example value for {{1}}" />
            )}
            {(headerType === 'IMAGE' || headerType === 'DOCUMENT') && (
              <>
                <Input aria-label="Header sample link" value={headerSample} onChange={(e) => setHeaderSample(e.target.value)} placeholder="https://example.com/sample.png" />
                <p className="text-[11px] text-steel">A public link to a sample {headerType === 'IMAGE' ? 'image' : 'document'} for review.</p>
              </>
            )}
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
            <p className="text-[11px] text-steel">{footer.length}/{TEMPLATE_LIMITS.footer} characters.</p>
          </div>
          <div className="space-y-2">
            <Label>Buttons (optional)</Label>
            {buttons.map((b, i) => (
              <div key={i} className="space-y-1 rounded-sm border border-line p-2">
                <div className="flex items-center justify-between text-[11px] uppercase text-steel">
                  <span>{b.type === 'QUICK_REPLY' ? 'Quick reply' : b.type === 'URL' ? 'Link' : 'Phone'}</span>
                  <Button variant="ghost" size="icon" aria-label={`Remove button ${i + 1}`} onClick={() => setButtons((l) => l.filter((_, idx) => idx !== i))}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <Input aria-label={`Button ${i + 1} text`} value={b.text} maxLength={TEMPLATE_LIMITS.button} onChange={(e) => updateButton(i, { text: e.target.value })} placeholder="Button text" />
                {b.type === 'URL' && (
                  <>
                    <Input aria-label={`Button ${i + 1} link`} value={b.url ?? ''} onChange={(e) => updateButton(i, { url: e.target.value })} placeholder="https://example.com/track/{{1}}" />
                    {(b.url ?? '').includes('{{') && (
                      <Input aria-label={`Button ${i + 1} example link`} value={b.example ?? ''} onChange={(e) => updateButton(i, { example: e.target.value })} placeholder="https://example.com/track/123" />
                    )}
                  </>
                )}
                {b.type === 'PHONE_NUMBER' && (
                  <Input aria-label={`Button ${i + 1} phone`} value={b.phone_number ?? ''} onChange={(e) => updateButton(i, { phone_number: e.target.value })} placeholder="+15551234567" />
                )}
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" disabled={quickCount >= TEMPLATE_LIMITS.quickReplies} onClick={() => addButton('QUICK_REPLY')}>
                Add quick reply
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={linkCount >= TEMPLATE_LIMITS.cta} onClick={() => addButton('URL')}>
                Add link
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={linkCount >= TEMPLATE_LIMITS.cta || buttons.some((b) => b.type === 'PHONE_NUMBER')} onClick={() => addButton('PHONE_NUMBER')}>
                Add phone
              </Button>
            </div>
            <p className="text-[11px] text-steel">Up to {TEMPLATE_LIMITS.quickReplies} quick replies, or {TEMPLATE_LIMITS.cta} link or phone buttons.</p>
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
  const [headerValue, setHeaderValue] = useState('');
  const [urlSuffixes, setUrlSuffixes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const holes = extractPlaceholders(template?.template || '');
  const headerType = template?.header_type || '';
  const headerNeedsText = headerType === 'TEXT' && extractPlaceholders(template?.header || '').length > 0;
  const headerNeedsMedia = headerType === 'IMAGE' || headerType === 'DOCUMENT';
  const dynamicUrlButtons = (template?.buttons ?? [])
    .map((b, index) => ({ b, index }))
    .filter(({ b }) => b.button_type === 'Visit Website' && b.url_type === 'Dynamic');

  const send = async () => {
    if (!template) return;
    setBusy(true);
    setErr('');
    const header_params: HeaderParam[] = headerNeedsText
      ? [headerValue]
      : headerNeedsMedia && headerValue.trim()
        ? [{ type: headerType === 'IMAGE' ? 'image' : 'document', link: headerValue.trim() }]
        : [];
    const button_params: ButtonParam[] = dynamicUrlButtons.map(({ index }) => ({ index, sub_type: 'url', text: urlSuffixes[index] ?? '' }));
    const res = await sendTemplateMessage({
      to,
      template_name: template.template_name || template.name,
      body_params: holes.map((_, i) => params[i] ?? ''),
      header_params,
      button_params,
      account: account || template.whatsapp_account,
    });
    setBusy(false);
    if (res.success) {
      toast.success('Template sent');
      setTo(''); setParams([]); setHeaderValue(''); setUrlSuffixes({});
      onClose();
    } else setErr(errorText(res));
  };

  return (
    <Dialog open={!!template} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Send template</DialogTitle>
          <DialogDescription className="font-mono">{template?.template_name}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="wa-send-to">Recipient</Label>
            <Input id="wa-send-to" value={to} onChange={(e) => setTo(e.target.value)} placeholder="+15551234567" className="font-mono" />
          </div>
          {headerNeedsText && (
            <div className="space-y-1">
              <Label htmlFor="wa-send-header">Header value</Label>
              <Input id="wa-send-header" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)} />
            </div>
          )}
          {headerNeedsMedia && (
            <div className="space-y-1">
              <Label htmlFor="wa-send-header">{headerType === 'IMAGE' ? 'Header image link' : 'Header document link'}</Label>
              <Input id="wa-send-header" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)} placeholder="https://example.com/file" />
            </div>
          )}
          {holes.map((n, i) => (
            <div key={n} className="space-y-1">
              <Label htmlFor={`wa-send-p-${n}`}>{`{{${n}}}`}</Label>
              <Input id={`wa-send-p-${n}`} value={params[i] ?? ''} onChange={(e) => setParams((p) => { const next = [...p]; next[i] = e.target.value; return next; })} />
            </div>
          ))}
          {dynamicUrlButtons.map(({ b, index }) => (
            <div key={index} className="space-y-1">
              <Label htmlFor={`wa-send-url-${index}`}>{`${b.button_label || 'Link'} (end of link)`}</Label>
              <Input id={`wa-send-url-${index}`} value={urlSuffixes[index] ?? ''} onChange={(e) => setUrlSuffixes((u) => ({ ...u, [index]: e.target.value }))} />
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
