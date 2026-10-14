import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { PageFrame } from '@/layouts/PageFrame';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import {
  checkToken,
  getSetupStatus,
  type GatewayHealthStatus,
  type SetupStatusResponse,
  type TokenCheckSummary,
} from '@/services/whatsappHealthApi';
import { BridgeUnavailable, LoadError } from '@/components/whatsapp/BridgeUnavailable';
import { TemplatesTab } from '@/components/whatsapp/TemplatesTab';
import { MessageLogTab } from '@/components/whatsapp/MessageLogTab';
import { GatewaySwitcher } from '@/components/whatsapp/overview/GatewaySwitcher';
import { OverviewTab } from '@/components/whatsapp/overview/OverviewTab';
import { SettingsTab } from '@/components/whatsapp/overview/SettingsTab';

const TABS = ['overview', 'messages', 'templates', 'settings'] as const;
type TabName = (typeof TABS)[number];

/** In-app checklist anchors: Settings sections, or the Messages tab for the test message. */
const SETTINGS_ANCHORS = new Set(['credentials', 'app-secret', 'enabled']);

const asTab = (v: string | null): TabName => (TABS.find((t) => t === v) ?? 'overview');

export default function WhatsAppManagePage() {
  const [params, setParams] = useSearchParams();
  const tab = asTab(params.get('tab'));
  const gatewayParam = params.get('gateway') || undefined;

  const [summary, setSummary] = useState<SetupStatusResponse | null>(null);
  const [summaryError, setSummaryError] = useState('');
  const [detail, setDetail] = useState<SetupStatusResponse | null>(null);
  const [detailError, setDetailError] = useState('');
  const [tokenCheck, setTokenCheck] = useState<{ gateway: string; result: TokenCheckSummary } | null>(null);
  const [checking, setChecking] = useState(false);
  const [anchor, setAnchor] = useState<string | null>(null);
  const detailRequest = useRef(0);

  const loadSummary = useCallback(async () => {
    setSummaryError('');
    const res = await getSetupStatus();
    if (res.ok) setSummary(res.data);
    else setSummaryError(res.error);
  }, []);
  useEffect(() => { void loadSummary(); }, [loadSummary]);

  const gateways = summary?.gateways ?? [];
  const gateway = (gateways.find((g) => g.name === gatewayParam) ?? gateways[0])?.name;

  const update = useCallback((patch: { tab?: TabName; gateway?: string }) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (patch.tab) next.set('tab', patch.tab);
      if (patch.gateway) next.set('gateway', patch.gateway);
      return next;
    }, { replace: true });
  }, [setParams]);

  // Keep the resolved gateway in the URL so reloads and shared links land on the same one.
  useEffect(() => {
    if (gateway && gateway !== gatewayParam) update({ gateway });
  }, [gateway, gatewayParam, update]);

  const loadDetail = useCallback(async (name: string) => {
    const id = ++detailRequest.current;
    setDetailError('');
    const res = await getSetupStatus(name);
    if (id !== detailRequest.current) return;
    if (res.ok && res.data.gateway) setDetail(res.data);
    else setDetailError(res.ok ? 'Could not load this gateway.' : res.error);
  }, []);
  useEffect(() => {
    setDetail(null);
    if (gateway) void loadDetail(gateway);
  }, [gateway, loadDetail]);

  const runTokenCheck = useCallback(async (name: string): Promise<TokenCheckSummary> => {
    const res = await checkToken(name);
    if (!res.ok) throw new Error(res.error);
    const result = res.data as TokenCheckSummary;
    setTokenCheck({ gateway: name, result });
    return result;
  }, []);

  const onCheck = async () => {
    if (!gateway) return;
    setChecking(true);
    await loadDetail(gateway);
    try {
      await runTokenCheck(gateway);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not check the token.');
    }
    setChecking(false);
  };

  const health: GatewayHealthStatus | null = detail?.gateway
    ? { ...detail.gateway, token_check: tokenCheck?.gateway === gateway ? tokenCheck.result : detail.gateway.token_check }
    : null;
  const settingsHref = gateway ? `/app/gateway/${encodeURIComponent(gateway)}` : '';
  const connectorAvailable = summary?.connector_available ?? true;

  // Checklist and next-step links like "#credentials" jump to the matching tab and section.
  const onAnchorClick = (e: MouseEvent) => {
    const link = (e.target as HTMLElement).closest('a');
    const id = link?.hash.slice(1);
    if (!link || !id || link.pathname !== window.location.pathname || link.target === '_blank') return;
    if (SETTINGS_ANCHORS.has(id)) update({ tab: 'settings' });
    else if (id === 'test-message') update({ tab: 'messages' });
    else return;
    e.preventDefault();
    if (SETTINGS_ANCHORS.has(id)) setAnchor(id);
  };
  useEffect(() => {
    if (!anchor || tab !== 'settings' || !health) return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(anchor)?.scrollIntoView?.({ block: 'start' });
      setAnchor(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [anchor, tab, health]);

  const loadingSummary = !summary && !summaryError;
  const gatewayQuery = gateway ? `&gateway=${encodeURIComponent(gateway)}` : '';

  return (
    <PageFrame
      title="Manage WhatsApp"
      actions={
        summary ? <GatewaySwitcher gateways={gateways} value={gateway} onChange={(g) => update({ gateway: g })} /> : undefined
      }
    >
      <div className="space-y-3 p-4" onClickCapture={onAnchorClick}>
        {summaryError ? (
          <LoadError message={summaryError} onRetry={loadSummary} />
        ) : loadingSummary ? (
          <Skeleton className="h-40" />
        ) : (
          <Tabs value={tab} onValueChange={(v) => update({ tab: asTab(v) })}>
            <TabsList className="w-full justify-start overflow-x-auto sm:w-auto">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="messages">Messages</TabsTrigger>
              <TabsTrigger value="templates">Templates</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>
            {!gateway ? (
              <p className="pt-4 text-xs text-steel">Add a WhatsApp gateway to see its health, messages and templates.</p>
            ) : (
              <>
                <TabsContent value="overview" className="pt-3">
                  {detailError ? (
                    <LoadError message={detailError} onRetry={() => loadDetail(gateway)} />
                  ) : (
                    <OverviewTab
                      gateway={health}
                      loading={!health}
                      checking={checking}
                      onCheck={onCheck}
                      onTurnOn={() => window.open(settingsHref, '_blank', 'noopener,noreferrer')}
                      settingsHref={settingsHref}
                      failedEventHref={() => `?tab=messages${gatewayQuery}`}
                    />
                  )}
                </TabsContent>
                <TabsContent value="messages" className="pt-3">
                  <MessageLogTab gateway={gateway} bridgeAvailable={connectorAvailable} />
                </TabsContent>
                <TabsContent value="templates" className="pt-3">
                  {connectorAvailable ? <TemplatesTab gateway={gateway} /> : <BridgeUnavailable />}
                </TabsContent>
                <TabsContent value="settings" className="pt-3">
                  {detailError ? (
                    <LoadError message={detailError} onRetry={() => loadDetail(gateway)} />
                  ) : health ? (
                    <SettingsTab
                      gateway={health}
                      accounts={detail?.accounts ?? []}
                      connectorAvailable={connectorAvailable}
                      onCheckToken={() => runTokenCheck(gateway)}
                      settingsHref={settingsHref}
                    />
                  ) : (
                    <Skeleton className="h-64" />
                  )}
                </TabsContent>
              </>
            )}
          </Tabs>
        )}
      </div>
    </PageFrame>
  );
}
