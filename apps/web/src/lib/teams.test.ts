import { describe, expect, it } from 'vitest';
import { colorForTeam, FALLBACK_TEAM_COLOR } from './teams';

describe('colorForTeam', () => {
	it('matches the long feed names by substring', () => {
		expect(colorForTeam('Red Bull Racing')).toBe('#3671C6');
		expect(colorForTeam('Kick Sauber')).toBe('#52E252');
		expect(colorForTeam('Haas F1 Team')).toBe('#B6BABD');
	});

	it('gives RB its own colour rather than the neutral fallback', () => {
		// The live feed's short name for Racing Bulls. Falling through to the
		// fallback grey made it indistinguishable from Haas on every
		// team-coloured chart.
		const rb = colorForTeam('RB');
		expect(rb).not.toBe(FALLBACK_TEAM_COLOR);
		expect(rb).not.toBe(colorForTeam('Haas F1 Team'));
		expect(rb).toBe(colorForTeam('Racing Bulls'));
	});

	it('is case- and whitespace-insensitive', () => {
		expect(colorForTeam('  rb ')).toBe(colorForTeam('RB'));
		expect(colorForTeam('FERRARI')).toBe(colorForTeam('Ferrari'));
	});

	it('gives every 2024 team a distinct colour', () => {
		const teams = [
			'Red Bull Racing',
			'Ferrari',
			'Mercedes',
			'McLaren',
			'Aston Martin',
			'Alpine',
			'Williams',
			'RB',
			'Kick Sauber',
			'Haas F1 Team'
		];
		const colors = teams.map(colorForTeam);
		expect(new Set(colors).size).toBe(teams.length);
	});

	it('falls back for an unknown or missing team', () => {
		expect(colorForTeam(null)).toBe(FALLBACK_TEAM_COLOR);
		expect(colorForTeam('Some New Team')).toBe(FALLBACK_TEAM_COLOR);
	});
});
