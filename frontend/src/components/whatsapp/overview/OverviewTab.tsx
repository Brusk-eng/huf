import { Skeleton } from '@/components/ui/skeleton';
import type { GatewayHealthStatus } from '@/services/whatsappHealthApi';
import { ActivityCard } from './ActivityCard';
import { ConnectionCard } from './ConnectionCard';
import { HealthBanner } from './HealthBanner';
import { SetupChecklist } from './SetupChecklist';
import { TokenCard } from './TokenCard';

export interface OverviewTabProps {
	gateway: GatewayHealthStatus | null;
	loading: boolean;
	checking: boolean;
	onCheck: () => void;
	onTurnOn: () => void;
	/** Desk form for this gateway (verify token and credentials live there). */
	settingsHref: string;
	/** Message log link for a failed event. */
	failedEventHref: (eventName: string) => string;
}

export function OverviewTab({ gateway, loading, checking, onCheck, onTurnOn, settingsHref, failedEventHref }: OverviewTabProps) {
	return (
		<div className="space-y-4">
			<HealthBanner gateway={gateway} loading={loading} checking={checking} onCheck={onCheck} onTurnOn={onTurnOn} />
			{loading || !gateway ? (
				<div className="grid gap-4 lg:grid-cols-2" aria-hidden>
					<Skeleton className="h-64" />
					<Skeleton className="h-64" />
				</div>
			) : (
				<>
					<div className="grid items-start gap-4 lg:grid-cols-2">
						<SetupChecklist items={gateway.checklist} health={gateway.health} />
						<ConnectionCard
							webhookUrl={gateway.webhook_url}
							verifyTokenPresent={gateway.verify_token_present}
							phoneNumberId={gateway.phone_number_id}
							phone={gateway.token_check?.phone}
							metaLinks={gateway.meta_links}
							integrationSettingsHref={settingsHref}
						/>
					</div>
					<div className="grid items-start gap-4 lg:grid-cols-2">
					<TokenCard check={gateway.token_check} />
					<ActivityCard
						lastEventAt={gateway.last_event_at}
						lastError={gateway.last_error}
						events24h={gateway.events_24h}
						lastFailedEvent={gateway.last_failed_event}
						unroutedPhoneIds={gateway.unrouted_phone_ids}
						failedEventHref={failedEventHref}
					/>
					</div>
				</>
			)}
		</div>
	);
}
