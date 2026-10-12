import { describe, expect, it } from 'vitest';
import {
	defaultCutoff,
	hiddenRounds,
	leaderSentence,
	progressionChart,
	readWatched,
	storeWatched,
	type DriverStanding,
	type StandingsRound
} from './view';

const DAY = 86_400_000;
const NOW = Date.parse('2026-10-11T18:00:00Z');

const round = (n: number, daysAgo: number | null): StandingsRound => ({
	round: n,
	event_name: `Round ${n}`,
	sprint: false,
	race_start_utc: daysAgo === null ? null : new Date(NOW - daysAgo * DAY).toISOString()
});

describe('the spoiler shield', () => {
	it('leaves out a race from the last week until the viewer says otherwise', () => {
		expect(defaultCutoff([round(15, 15), round(16, 7.5), round(17, 0.3)], NOW)).toBe(16);
		expect(defaultCutoff([round(15, 15), round(16, 6), round(17, 0.3)], NOW)).toBe(15);
	});

	it('shows a season long over in full', () => {
		expect(defaultCutoff([round(1, 400), round(2, 390)], NOW)).toBe(2);
	});

	it('never assumes an undated latest round is safe', () => {
		expect(defaultCutoff([round(1, null), round(2, null)], NOW)).toBe(1);
		expect(defaultCutoff([round(1, 0.5)], NOW)).toBe(0);
		expect(defaultCutoff([], NOW)).toBe(0);
	});

	it('remembers the viewer’s choice per season, and survives blocked storage', () => {
		const store = new Map<string, string>();
		const storage = {
			getItem: (k: string) => store.get(k) ?? null,
			setItem: (k: string, v: string) => void store.set(k, v)
		};
		expect(readWatched(2026, storage)).toBeNull();
		storeWatched(2026, 17, storage);
		expect(readWatched(2026, storage)).toBe(17);
		expect(readWatched(2025, storage)).toBeNull();
		const blocked = {
			getItem: () => {
				throw new Error('blocked');
			},
			setItem: () => {
				throw new Error('blocked');
			}
		};
		expect(readWatched(2026, blocked)).toBeNull();
		expect(() => storeWatched(2026, 3, blocked)).not.toThrow();
	});

	it('lists what it hides', () => {
		const rounds = [round(15, 15), round(16, 7.5), round(17, 0.3)];
		expect(hiddenRounds(rounds, 15).map((r) => r.round)).toEqual([16, 17]);
	});
});

const driver = (
	code: string,
	points: number,
	team: string,
	by_round: number[] = [points]
): DriverStanding => ({
	position: 0,
	code,
	name: code.charAt(0) + code.slice(1).toLowerCase(),
	team,
	team_color: '#123456',
	points,
	wins: 0,
	by_round
});

describe('leaderSentence', () => {
	it('gives the margin and the points still to race for', () => {
		const drivers = [driver('VER', 136, 'RB'), driver('PER', 103, 'RB')];
		expect(
			leaderSentence(drivers, {
				races: 18,
				sprints: 4,
				race_points: 26,
				sprint_points: 8,
				max_points: 500
			})
		).toBe(
			'Ver leads Per by 33. Up to 500 points are still available: 18 races at 26 and 4 sprints at 8.'
		);
	});

	it('says when the leader can no longer be caught', () => {
		const drivers = [driver('VER', 400, 'RB'), driver('NOR', 340, 'MCL')];
		const remaining = { races: 2, sprints: 0, race_points: 25, sprint_points: 8, max_points: 50 };
		expect(leaderSentence(drivers, remaining)).toContain('Ver can no longer be caught.');
	});

	it('handles a level start and the end of a season', () => {
		const level = [driver('AAA', 0, 'A'), driver('BBB', 0, 'B')];
		expect(leaderSentence(level, null)).toBe('Aaa and Bbb are level on 0.');
		const done = { races: 0, sprints: 0, race_points: 25, sprint_points: 8, max_points: 0 };
		const final = [driver('VER', 437, 'RB'), driver('NOR', 374, 'MCL')];
		expect(leaderSentence(final, done)).toBe('Ver took the title by 63.');
	});
});

describe('progressionChart', () => {
	const drivers = [
		driver('VER', 136, 'RB', [26, 51, 136]),
		driver('PER', 103, 'RB', [18, 36, 103]),
		driver('LEC', 98, 'FER', [12, 28, 98]),
		driver('NOR', 97, 'MCL', [2, 20, 97])
	];
	const chart = progressionChart(drivers, ['R1', 'R2', 'R3'], 600, 220)!;

	it('draws one line per driver, a team’s second car dashed', () => {
		expect(chart.lines.map((l) => [l.code, l.dashed])).toEqual([
			['VER', false],
			['PER', true],
			['LEC', false],
			['NOR', false]
		]);
		// Highest total at the top of the plot.
		expect(chart.lines[0]!.points.split(' ').at(-1)).toBe('530,10');
	});

	it('keeps close end labels apart', () => {
		const ys = chart.lines.map((l) => l.label.y).sort((a, b) => a - b);
		for (let i = 1; i < ys.length; i++) expect(ys[i]! - ys[i - 1]!).toBeGreaterThanOrEqual(11.9);
	});

	it('has nothing to draw before any round counts', () => {
		expect(progressionChart([driver('VER', 0, 'RB', [])], [], 600, 220)).toBeNull();
	});
});
