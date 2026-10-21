import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { healthLabel, type HealthState } from '@/services/whatsappHealthApi';

export interface GatewayOption {
	name: string;
	health: HealthState;
	enabled: boolean;
}

export interface GatewaySwitcherProps {
	gateways: GatewayOption[];
	value: string | undefined;
	onChange: (name: string) => void;
}

const DOT_COLOR: Record<HealthState, string> = {
	healthy: 'bg-good',
	degraded: 'bg-warning',
	blocked: 'bg-destructive',
	not_set_up: 'bg-steel-soft',
	disabled: 'bg-steel-soft',
};

function HealthDot({ health }: { health: HealthState }) {
	return (
		<span
			role="img"
			aria-label={healthLabel(health)}
			data-health={health}
			className={cn('inline-block h-2 w-2 shrink-0 rounded-full', DOT_COLOR[health])}
		/>
	);
}

export function GatewaySwitcher({ gateways, value, onChange }: GatewaySwitcherProps) {
	if (gateways.length === 0) {
		return (
			<div className="flex flex-wrap items-center gap-2 text-xs text-steel">
				<span>No WhatsApp gateway yet</span>
				<Button asChild size="sm" variant="outline">
					<Link to="/gateways">Open Gateways</Link>
				</Button>
			</div>
		);
	}
	return (
		<Select value={value} onValueChange={onChange}>
			<SelectTrigger className="w-full sm:w-64" aria-label="WhatsApp gateway">
				<SelectValue placeholder="Choose a gateway" />
			</SelectTrigger>
			<SelectContent>
				{gateways.map((g) => (
					<SelectItem key={g.name} value={g.name}>
						<span className="flex items-center gap-2">
							<HealthDot health={g.enabled ? g.health : 'disabled'} />
							{g.name}
						</span>
					</SelectItem>
				))}
			</SelectContent>
		</Select>
	);
}
