import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  errorText, isBridgeUnavailable, listConversations, listMessages, sendTextMessage,
  type WhatsAppConversation, type WhatsAppError, type WhatsAppMessage,
} from '@/services/whatsappApi';
import { BridgeUnavailable, LoadError } from './BridgeUnavailable';

export function MessagesTab({ account, gateway }: { account?: string; gateway?: string }) {
  const [convs, setConvs] = useState<WhatsAppConversation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<WhatsAppMessage[]>([]);
  const [loadingConvs, setLoadingConvs] = useState(true);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<WhatsAppError | null>(null);

  const loadConvs = useCallback(async () => {
    setLoadingConvs(true);
    setError('');
    const res = await listConversations(account);
    if (res.success) setConvs(res.conversations);
    else if (isBridgeUnavailable(res)) setUnavailable(true);
    else setError(errorText(res));
    setLoadingConvs(false);
  }, [account]);

  const loadMsgs = useCallback(async (phone: string) => {
    setLoadingMsgs(true);
    const res = await listMessages(account, phone);
    if (res.success) setMessages([...res.messages].reverse());
    else if (isBridgeUnavailable(res)) setUnavailable(true);
    else setError(errorText(res));
    setLoadingMsgs(false);
  }, [account]);

  useEffect(() => { void loadConvs(); }, [loadConvs]);
  useEffect(() => { if (selected) void loadMsgs(selected); }, [selected, loadMsgs]);

  const send = async () => {
    if (!selected || !gateway) return;
    setSending(true);
    setSendError(null);
    const res = await sendTextMessage(gateway, selected, text);
    setSending(false);
    if (res.success) {
      setText('');
      toast.success('Message sent');
      void loadMsgs(selected);
    } else if (isBridgeUnavailable(res)) setUnavailable(true);
    else setSendError(res);
  };

  if (unavailable) return <BridgeUnavailable />;

  return (
    <div className="grid gap-4 md:grid-cols-[260px_1fr]">
      <div className="space-y-2">
        {error && <LoadError message={error} onRetry={loadConvs} />}
        {loadingConvs ? (
          <div className="flex items-center gap-2 text-xs text-steel"><Loader2 className="h-4 w-4 animate-spin" /> Loading conversations...</div>
        ) : convs.length === 0 && !error ? (
          <p className="rounded-sm border border-line p-4 text-center text-xs text-steel">No conversations yet.</p>
        ) : (
          <ul className="divide-y divide-line rounded-sm border border-line">
            {convs.map((c) => (
              <li key={c.phone}>
                <button
                  type="button"
                  onClick={() => { setSelected(c.phone); setSendError(null); }}
                  className={cn('w-full px-3 py-2 text-left hover:bg-paper-deep', selected === c.phone && 'bg-paper-deep')}
                >
                  <div className="flex justify-between text-xs">
                    <span className="font-mono text-ink">{c.profile_name || c.phone}</span>
                    <span className="text-steel">{c.count}</span>
                  </div>
                  <p className="truncate text-[11px] text-steel">{c.last_message?.message}</p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3">
        {!selected ? (
          <p className="rounded-sm border border-line p-6 text-center text-xs text-steel">Select a conversation.</p>
        ) : (
          <>
            {loadingMsgs ? (
              <div className="flex items-center gap-2 text-xs text-steel"><Loader2 className="h-4 w-4 animate-spin" /> Loading messages...</div>
            ) : messages.length === 0 ? (
              <p className="text-xs text-steel">No messages.</p>
            ) : (
              <ul className="max-h-[420px] space-y-2 overflow-y-auto rounded-sm border border-line p-3">
                {messages.map((m) => (
                  <li key={m.name} className={cn('max-w-[80%] rounded-sm border border-line px-2 py-1 text-xs', m.type === 'Outgoing' ? 'ml-auto bg-paper-deep' : '')}>
                    <p className="text-ink">{m.message}</p>
                    <p className="font-mono text-[10px] text-steel">{m.creation} {m.status ? `· ${m.status}` : ''}</p>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <Input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={gateway ? 'Type a message' : 'No WhatsApp gateway available to send from'}
                disabled={!gateway}
                aria-label="Message text"
                onKeyDown={(e) => { if (e.key === 'Enter' && text.trim() && !sending) void send(); }}
              />
              <Button onClick={send} disabled={!gateway || sending || !text.trim()} aria-label="Send message">
                {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </Button>
            </div>
            {sendError && (
              <p role="alert" className="text-xs text-destructive">
                {errorText(sendError)}
                {sendError.error === 'window_closed' &&
                  ' Free-form text is only allowed within 24 hours of the last customer message. Send an approved template from the Templates tab instead.'}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
