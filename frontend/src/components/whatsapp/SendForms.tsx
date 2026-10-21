import { useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  INTERACTIVE_LIMITS, sendContactsMessage, sendInteractiveMessage, sendLocationMessage, sendMediaMessage,
  sendReactionMessage, sendStickerMessage, sendTextMessage, sendVoiceNote, uploadPublicFile, validateInteractive, validateMediaSend,
  validateVoiceNote,
  type InteractiveButton, type InteractiveKind, type MediaKind, type WhatsAppError,
} from '@/services/whatsappApi';
import { TemplateSend } from './TemplateSend';

export type SendType =
  | 'text' | 'template' | 'image' | 'document' | 'audio' | 'video' | 'sticker'
  | 'location' | 'contact' | 'reaction' | 'buttons' | 'list' | 'cta_url';

export const SEND_TYPES: { value: SendType; label: string; note: string }[] = [
  { value: 'text', label: 'Text', note: "Only within 24 hours of the customer's last message." },
  { value: 'template', label: 'Template', note: 'Works any time, including outside the 24-hour window.' },
  { value: 'image', label: 'Image', note: 'JPEG or PNG, up to 5 MB.' },
  { value: 'document', label: 'Document', note: 'Up to 100 MB.' },
  { value: 'audio', label: 'Audio', note: 'Up to 16 MB.' },
  { value: 'video', label: 'Video', note: 'MP4 or 3GP, up to 16 MB.' },
  { value: 'sticker', label: 'Sticker', note: 'WebP, up to 100 KB (500 KB if animated).' },
  { value: 'location', label: 'Location', note: 'A map pin.' },
  { value: 'contact', label: 'Contact', note: 'A contact card.' },
  { value: 'reaction', label: 'Reaction', note: 'React to a message the customer sent in the last 30 days.' },
  { value: 'buttons', label: 'Reply buttons', note: 'Up to 3 quick-reply buttons.' },
  { value: 'list', label: 'List', note: 'A menu of up to 10 choices.' },
  { value: 'cta_url', label: 'Link button', note: 'A message with one button that opens a link.' },
];

const MEDIA_TYPES: SendType[] = ['image', 'document', 'audio', 'video'];

interface FormProps {
  gateway: string;
  to: string;
  /** Runs a send; shows the shared error and refreshes the log on success. */
  run: (send: () => Promise<{ success: boolean } | WhatsAppError>, done: string, reset: () => void) => Promise<void>;
  sending: boolean;
  setError: (e: WhatsAppError | null) => void;
}

function SendButton({ sending, disabled, onClick }: { sending: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <Button size="sm" onClick={onClick} disabled={sending || disabled}>
      {sending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}Send
    </Button>
  );
}

const bad = (message: string): WhatsAppError => ({ success: false, error: 'invalid_params', message });

function TextForm({ gateway, to, run, sending }: FormProps) {
  const [text, setText] = useState('');
  return (
    <div className="space-y-2">
      <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a message" aria-label="Message text" rows={3} />
      <SendButton sending={sending} disabled={!text.trim() || !to.trim()} onClick={() => void run(() => sendTextMessage(gateway, to, text), 'Message sent', () => setText(''))} />
    </div>
  );
}

function MediaForm({ kind, gateway, to, run, sending, setError }: FormProps & { kind: MediaKind | 'sticker' }) {
  const [link, setLink] = useState('');
  const [file, setFile] = useState<{ fileUrl: string; name: string } | null>(null);
  const [caption, setCaption] = useState('');
  const [uploading, setUploading] = useState(false);
  const [voice, setVoice] = useState(false);

  const pick = async (f: File | undefined) => {
    if (!f) return;
    setUploading(true);
    setError(null);
    const res = await uploadPublicFile(f);
    setUploading(false);
    if ('fileUrl' in res) { setFile({ fileUrl: res.fileUrl, name: f.name }); setLink(''); } else setError(res);
  };
  const reset = () => { setLink(''); setFile(null); setCaption(''); setVoice(false); };
  const submit = () => {
    const asVoice = kind === 'audio' && voice;
    const problem = kind === 'sticker'
      ? (!link.trim() && !file ? 'Add a file or paste a public link' : null)
      : asVoice
        ? validateVoiceNote({ link, fileUrl: file?.fileUrl, fileName: file?.name })
        : validateMediaSend({ kind, link, fileUrl: file?.fileUrl, caption });
    if (problem) return setError(bad(problem));
    const fileUrl = link.trim() ? undefined : file?.fileUrl;
    void run(
      () => kind === 'sticker'
        ? sendStickerMessage(gateway, to, { link, fileUrl })
        : asVoice
        ? sendVoiceNote(gateway, to, { link, fileUrl })
        : sendMediaMessage(gateway, to, { kind, link, fileUrl, caption, filename: kind === 'document' ? file?.name : undefined }),
      'Message sent',
      reset,
    );
  };
  return (
    <div className="space-y-2">
      <Input value={link} onChange={(e) => { setLink(e.target.value); if (e.target.value) setFile(null); }} placeholder="Public link, https://..." aria-label="Media link" />
      <div className="flex items-center gap-2 text-xs text-steel">
        <label className="cursor-pointer rounded-sm border border-line px-2 py-1 hover:bg-paper-deep">
          {uploading ? 'Uploading...' : 'Choose file'}
          <input type="file" className="sr-only" aria-label="Media file" onChange={(e) => void pick(e.target.files?.[0])} />
        </label>
        <span className="truncate">{file ? file.name : 'or upload a file'}</span>
      </div>
      {(kind === 'image' || kind === 'video' || kind === 'document') && (
        <Input value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="Caption (optional)" aria-label="Media caption" />
      )}
      {kind === 'audio' && (
        <label className="flex items-center gap-2 text-xs text-steel">
          <input type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} aria-label="Send as voice note" />
          Send as a voice note (Ogg/Opus, mono, up to 16 MB)
        </label>
      )}
      <SendButton sending={sending} disabled={uploading || !to.trim()} onClick={submit} />
    </div>
  );
}

function LocationForm({ gateway, to, run, sending, setError }: FormProps) {
  const [lat, setLat] = useState('');
  const [lng, setLng] = useState('');
  const [name, setName] = useState('');
  const [address, setAddress] = useState('');
  const submit = () => {
    const latitude = Number(lat);
    const longitude = Number(lng);
    if (!lat.trim() || !lng.trim() || Number.isNaN(latitude) || Number.isNaN(longitude)) return setError(bad('Enter a latitude and longitude'));
    if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return setError(bad('Latitude is between -90 and 90, longitude between -180 and 180'));
    void run(() => sendLocationMessage(gateway, to, { latitude, longitude, name, address }), 'Location sent', () => { setLat(''); setLng(''); setName(''); setAddress(''); });
  };
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <Input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="Latitude" inputMode="decimal" aria-label="Latitude" />
        <Input value={lng} onChange={(e) => setLng(e.target.value)} placeholder="Longitude" inputMode="decimal" aria-label="Longitude" />
      </div>
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Place name (optional)" aria-label="Place name" />
      <Input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Address (optional)" aria-label="Address" />
      <SendButton sending={sending} disabled={!to.trim()} onClick={submit} />
    </div>
  );
}

function ContactForm({ gateway, to, run, sending, setError }: FormProps) {
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [org, setOrg] = useState('');
  const submit = () => {
    if (!name.trim()) return setError(bad('Add the contact name'));
    if (!phone.trim() && !email.trim()) return setError(bad('Add a phone number or an email'));
    void run(
      () => sendContactsMessage(gateway, to, [{
        name: { formatted_name: name.trim() },
        phones: phone.trim() ? [{ phone: phone.trim() }] : undefined,
        emails: email.trim() ? [{ email: email.trim() }] : undefined,
        org: org.trim() ? { company: org.trim() } : undefined,
      }]),
      'Contact sent',
      () => { setName(''); setPhone(''); setEmail(''); setOrg(''); },
    );
  };
  return (
    <div className="space-y-2">
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" aria-label="Contact name" />
      <div className="grid grid-cols-2 gap-2">
        <Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Phone" aria-label="Contact phone" />
        <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" aria-label="Contact email" />
      </div>
      <Input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Company (optional)" aria-label="Contact company" />
      <SendButton sending={sending} disabled={!to.trim()} onClick={submit} />
    </div>
  );
}

function ReactionForm({ gateway, to, run, sending, setError }: FormProps) {
  const [messageId, setMessageId] = useState('');
  const [emoji, setEmoji] = useState('');
  const submit = (remove: boolean) => {
    if (!messageId.trim()) return setError(bad('Add the ID of the message to react to'));
    if (!remove && !emoji.trim()) return setError(bad('Add an emoji'));
    void run(() => sendReactionMessage(gateway, to, messageId, emoji, remove), remove ? 'Reaction removed' : 'Reaction sent', () => setEmoji(''));
  };
  return (
    <div className="space-y-2">
      <Input value={messageId} onChange={(e) => setMessageId(e.target.value)} placeholder="Message ID (shown in message details)" className="font-mono" aria-label="Message ID" />
      <Input value={emoji} onChange={(e) => setEmoji(e.target.value)} placeholder="Emoji" className="w-24" aria-label="Emoji" />
      <div className="flex gap-2">
        <SendButton sending={sending} disabled={!to.trim()} onClick={() => submit(false)} />
        <Button size="sm" variant="outline" disabled={sending || !to.trim()} onClick={() => submit(true)}>Remove reaction</Button>
      </div>
    </div>
  );
}

function InteractiveForm({ kind, gateway, to, run, sending, setError }: FormProps & { kind: InteractiveKind }) {
  const [body, setBody] = useState('');
  const [header, setHeader] = useState('');
  const [footer, setFooter] = useState('');
  const [buttons, setButtons] = useState<InteractiveButton[]>([{ id: 'btn_1', title: '' }]);
  const [buttonText, setButtonText] = useState('');
  const [rows, setRows] = useState<{ title: string; description: string }[]>([{ title: '', description: '' }]);
  const [displayText, setDisplayText] = useState('');
  const [url, setUrl] = useState('');

  const submit = () => {
    const msg = {
      kind, body, header, footer, buttonText, displayText, url,
      buttons,
      sections: [{ title: 'Options', rows: rows.map((r, i) => ({ id: `row_${i + 1}`, title: r.title, description: r.description.trim() || undefined })) }],
    };
    const problem = validateInteractive(msg);
    if (problem) return setError(bad(problem));
    void run(() => sendInteractiveMessage(gateway, to, msg), 'Message sent', () => {
      setBody('');
      setHeader('');
      setFooter('');
      setButtons([{ id: 'btn_1', title: '' }]);
      setButtonText('');
      setRows([{ title: '', description: '' }]);
      setDisplayText('');
      setUrl('');
    });
  };
  return (
    <div className="space-y-2">
      <Input value={header} onChange={(e) => setHeader(e.target.value)} placeholder="Header (optional)" maxLength={60} aria-label="Header" />
      <Textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Message text" aria-label="Interactive message text" rows={2} />
      {kind === 'buttons' && (
        <div className="space-y-1.5">
          {buttons.map((b, i) => (
            <div key={b.id} className="flex gap-2">
              <Input value={b.title} maxLength={INTERACTIVE_LIMITS.buttonTitle} placeholder={`Button ${i + 1}`} aria-label={`Button ${i + 1} label`}
                onChange={(e) => setButtons((prev) => prev.map((x) => (x.id === b.id ? { ...x, title: e.target.value } : x)))} />
              {buttons.length > 1 && (
                <Button size="icon" variant="ghost" aria-label={`Remove button ${i + 1}`} onClick={() => setButtons((prev) => prev.filter((x) => x.id !== b.id))}><Trash2 className="h-4 w-4" /></Button>
              )}
            </div>
          ))}
          {buttons.length < INTERACTIVE_LIMITS.buttons && (
            <Button size="sm" variant="outline" onClick={() => setButtons((prev) => [...prev, { id: `btn_${Date.now()}`, title: '' }])}><Plus className="mr-1 h-3.5 w-3.5" />Add button</Button>
          )}
        </div>
      )}
      {kind === 'list' && (
        <div className="space-y-1.5">
          <Input value={buttonText} onChange={(e) => setButtonText(e.target.value)} maxLength={INTERACTIVE_LIMITS.ctaText} placeholder="Menu button label, for example Choose" aria-label="List button label" />
          {rows.map((r, i) => (
            <div key={i} className="flex gap-2">
              <Input value={r.title} maxLength={INTERACTIVE_LIMITS.rowTitle} placeholder={`Item ${i + 1}`} aria-label={`Item ${i + 1} title`}
                onChange={(e) => setRows((prev) => prev.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))} />
              <Input value={r.description} maxLength={72} placeholder="Description (optional)" aria-label={`Item ${i + 1} description`}
                onChange={(e) => setRows((prev) => prev.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} />
              {rows.length > 1 && (
                <Button size="icon" variant="ghost" aria-label={`Remove item ${i + 1}`} onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
              )}
            </div>
          ))}
          {rows.length < INTERACTIVE_LIMITS.listRows && (
            <Button size="sm" variant="outline" onClick={() => setRows((prev) => [...prev, { title: '', description: '' }])}><Plus className="mr-1 h-3.5 w-3.5" />Add item</Button>
          )}
        </div>
      )}
      {kind === 'cta_url' && (
        <div className="grid grid-cols-2 gap-2">
          <Input value={displayText} onChange={(e) => setDisplayText(e.target.value)} maxLength={INTERACTIVE_LIMITS.ctaText} placeholder="Button label" aria-label="Link button label" />
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." aria-label="Link address" />
        </div>
      )}
      <Input value={footer} onChange={(e) => setFooter(e.target.value)} placeholder="Footer (optional)" maxLength={60} aria-label="Footer" />
      <SendButton sending={sending} disabled={!to.trim()} onClick={submit} />
    </div>
  );
}

export function SendTypePicker({ value, onChange }: { value: SendType; onChange: (v: SendType) => void }) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as SendType)}>
      <SelectTrigger className="w-44" aria-label="Message type"><SelectValue /></SelectTrigger>
      <SelectContent>{SEND_TYPES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}</SelectContent>
    </Select>
  );
}

export function SendForm({ type, ...props }: FormProps & { type: SendType }) {
  if (type === 'text') return <TextForm {...props} />;
  if (type === 'template') return <TemplateSend gateway={props.gateway} to={props.to} />;
  if (MEDIA_TYPES.includes(type)) return <MediaForm key={type} kind={type as MediaKind} {...props} />;
  if (type === 'sticker') return <MediaForm kind="sticker" {...props} />;
  if (type === 'location') return <LocationForm {...props} />;
  if (type === 'contact') return <ContactForm {...props} />;
  if (type === 'reaction') return <ReactionForm {...props} />;
  return <InteractiveForm key={type} kind={type as InteractiveKind} {...props} />;
}
