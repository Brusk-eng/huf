// @vitest-environment jsdom
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { GatewaySwitcher } from './GatewaySwitcher';

beforeAll(() => {
	Element.prototype.hasPointerCapture = () => false;
	Element.prototype.scrollIntoView = () => {};
});

const gateways = [
	{ name: 'GW-1', health: 'healthy' as const, enabled: true },
	{ name: 'GW-2', health: 'blocked' as const, enabled: true },
	{ name: 'GW-3', health: 'healthy' as const, enabled: false },
];

describe('GatewaySwitcher', () => {
	it('lists gateways with a health dot and reports the selection', async () => {
		const onChange = vi.fn();
		render(
			<MemoryRouter>
				<GatewaySwitcher gateways={gateways} value="GW-1" onChange={onChange} />
			</MemoryRouter>
		);
		await userEvent.click(screen.getByRole('combobox', { name: 'WhatsApp gateway' }));
		expect(screen.getAllByRole('img').map((d) => d.getAttribute('data-health'))).toContain('blocked');
		expect(screen.getAllByRole('img', { name: 'Turned off' })).toHaveLength(1);
		await userEvent.click(screen.getByRole('option', { name: /GW-2/ }));
		expect(onChange).toHaveBeenCalledWith('GW-2');
	});

	it('shows an empty state linking to Gateways', () => {
		render(
			<MemoryRouter>
				<GatewaySwitcher gateways={[]} value={undefined} onChange={vi.fn()} />
			</MemoryRouter>
		);
		expect(screen.getByText('No WhatsApp gateway yet')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Open Gateways' })).toHaveAttribute('href', '/gateways');
	});
});
