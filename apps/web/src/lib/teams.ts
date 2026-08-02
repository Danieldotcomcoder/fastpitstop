/** Team colours are data, not brand. Render as a 3px leading bar or a small
 * swatch — never as a filled background behind text. Match on a lowercase
 * substring because feed team names drift across seasons/sponsors. */

const TEAM_COLORS: readonly (readonly [string, string])[] = [
	['red bull', '#3671C6'],
	['ferrari', '#E8002D'],
	['mercedes', '#27F4D2'],
	['mclaren', '#FF8000'],
	['aston martin', '#229971'],
	['alpine', '#FF87BC'],
	['williams', '#64C4FF'],
	['racing bulls', '#6692FF'],
	['alphatauri', '#6692FF'],
	['sauber', '#52E252'],
	['alfa romeo', '#C92D4B'],
	['haas', '#B6BABD']
];

/** Neutral — unrecognized teams must not look like a real team colour. */
export const FALLBACK_TEAM_COLOR = '#6B7684';

export function colorForTeam(team: string | null): string {
	if (!team) return FALLBACK_TEAM_COLOR;
	const normalized = team.toLowerCase();
	for (const [needle, color] of TEAM_COLORS) {
		if (normalized.includes(needle)) return color;
	}
	return FALLBACK_TEAM_COLOR;
}
