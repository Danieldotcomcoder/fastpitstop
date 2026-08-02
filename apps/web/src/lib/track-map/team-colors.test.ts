import { describe, expect, it } from 'vitest';
import { colorForTeam, FALLBACK_TEAM_COLOR } from './team-colors';

describe('colorForTeam', () => {
	it('matches a known team by exact name', () => {
		expect(colorForTeam('Ferrari')).not.toBe(FALLBACK_TEAM_COLOR);
	});

	it('matches a sponsor-prefixed variant of a known team', () => {
		expect(colorForTeam('Oracle Red Bull Racing')).toBe(colorForTeam('Red Bull Racing'));
	});

	it('is case-insensitive', () => {
		expect(colorForTeam('mercedes')).toBe(colorForTeam('Mercedes'));
	});

	it('falls back for an unrecognized team rather than guessing', () => {
		expect(colorForTeam('Some New Team')).toBe(FALLBACK_TEAM_COLOR);
	});

	it('falls back for null (team not yet known)', () => {
		expect(colorForTeam(null)).toBe(FALLBACK_TEAM_COLOR);
	});

	it('returns valid CSS hex colors for every entry', () => {
		const teams = ['Ferrari', 'Mercedes', 'Red Bull', 'McLaren', 'Aston Martin', 'Alpine'];
		for (const team of teams) {
			expect(colorForTeam(team)).toMatch(/^#[0-9a-f]{6}$/i);
		}
	});
});
