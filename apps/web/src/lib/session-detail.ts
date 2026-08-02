/** Sticky sub-nav sections on `/sessions/[year]/[round]`. Order is product. */

export const SESSION_SECTIONS = [
	{ id: 'story', label: 'Race story', shortcut: '1' },
	{ id: 'shape', label: 'Race shape', shortcut: '2' },
	{ id: 'pace', label: 'Pace & degradation', shortcut: '3' },
	{ id: 'strategy', label: 'Strategy', shortcut: '4' },
	{ id: 'radio', label: 'Radio', shortcut: '5' },
	{ id: 'braking', label: 'Braking drift', shortcut: '6' },
	{ id: 'method', label: 'Method', shortcut: '7' }
] as const;

export type SessionSectionId = (typeof SESSION_SECTIONS)[number]['id'];

export function parseDriversParam(raw: string | null): string[] {
	if (!raw) return [];
	return raw
		.split(',')
		.map((d) => d.trim())
		.filter(Boolean);
}

export function driversParam(drivers: readonly string[]): string {
	return drivers.join(',');
}
