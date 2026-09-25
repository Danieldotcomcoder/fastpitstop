import { SHOW_RACE_STORY, SHOW_RADIO } from './features';
/** Sticky sub-nav sections on `/sessions/[year]/[round]`. Order is product. */

const ALL_SECTIONS = [
	{ id: 'classification', label: 'Classification', shortcut: '1' },
	{ id: 'story', label: 'Race story', shortcut: '1' },
	{ id: 'shape', label: 'Race positions', shortcut: '2' },
	{ id: 'pace', label: 'Tyre performance', shortcut: '3' },
	{ id: 'strategy', label: 'Strategy', shortcut: '4' },
	{ id: 'radio', label: 'Radio', shortcut: '5' },
	{ id: 'braking', label: 'Braking drift', shortcut: '5' },
	{ id: 'method', label: 'Method', shortcut: '6' }
] as const;

export const SESSION_SECTIONS = ALL_SECTIONS.filter(
	(s) => (s.id !== 'story' || SHOW_RACE_STORY) && (s.id !== 'radio' || SHOW_RADIO)
);
export type SessionSectionId = (typeof ALL_SECTIONS)[number]['id'];

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
