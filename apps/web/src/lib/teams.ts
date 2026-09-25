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

/** Team names the feed sends as a short form no substring rule can catch.
 * Matched EXACTLY (after lowercasing/trimming) and before the substring
 * pass, because a two-letter name would otherwise both miss its own colour
 * and risk matching inside a longer one. "RB" is the live feed's own name
 * for the team the historical tables call "Racing Bulls" — without this it
 * fell through to the neutral grey, which is also Haas's real colour, so
 * two different teams drew identically on every team-coloured chart. */
const TEAM_EXACT: Record<string, string> = {
	rb: '#6692FF',
	'vcarb': '#6692FF',
	haas: '#B6BABD'
};

/** Neutral — unrecognized teams must not look like a real team colour. */
export const FALLBACK_TEAM_COLOR = '#6B7684';

export function colorForTeam(team: string | null): string {
	if (!team) return FALLBACK_TEAM_COLOR;
	const normalized = team.toLowerCase().trim();
	const exact = TEAM_EXACT[normalized];
	if (exact !== undefined) return exact;
	for (const [needle, color] of TEAM_COLORS) {
		if (normalized.includes(needle)) return color;
	}
	return FALLBACK_TEAM_COLOR;
}
