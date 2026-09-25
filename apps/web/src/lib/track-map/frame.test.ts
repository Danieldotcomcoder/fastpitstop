import { describe, expect, it } from 'vitest';
import { buildArcLengthTable } from './arc-length';
import { createRaceState, type RaceState } from './car-state';
import { DominanceTracker } from './dominance';
import { buildDrawFrame } from './frame';
import { colorForTeam } from './team-colors';
import type { Point } from './types';

function raceStateWith(
	driver: string,
	x: number,
	y: number,
	tsMs: number,
	team: string | null
): RaceState {
	const state = createRaceState();
	state.cars.set(driver, {
		driver,
		code: 'HAM',
		team,
		compound: 'soft',
		current: { x, y, tsMs },
		previous: null
	});
	return state;
}

const SQUARE: Point[] = [
	[0, 0],
	[1, 0],
	[1, 1],
	[0, 1],
	[0, 0]
];

describe('buildDrawFrame', () => {
	it('passes the outline through untouched', () => {
		const frame = buildDrawFrame(SQUARE, null, null, createRaceState(), 0);
		expect(frame.outline).toBe(SQUARE);
	});

	it('produces no segments without an arc table or dominance tracker', () => {
		const frame = buildDrawFrame(SQUARE, null, null, createRaceState(), 0);
		expect(frame.segments).toEqual([]);
	});

	it('draws one car per marker with a resolved team color', () => {
		const state = raceStateWith('44', 0.5, 0.5, 1000, 'Mercedes');
		const frame = buildDrawFrame(SQUARE, null, null, state, 1000);

		expect(frame.cars).toHaveLength(1);
		expect(frame.cars[0]).toMatchObject({
			driver: '44',
			code: 'HAM',
			color: colorForTeam('Mercedes'),
			x: 0.5,
			y: 0.5,
			stale: false
		});
	});

	it('omits a driver with no known position yet', () => {
		const state = createRaceState();
		state.cars.set('1', {
			driver: '1',
			code: null,
			team: null,
			compound: null,
			current: null,
			previous: null
		});

		const frame = buildDrawFrame(SQUARE, null, null, state, 1000);
		expect(frame.cars).toEqual([]);
	});

	it('interpolates a car between two samples by default', () => {
		const state = createRaceState();
		state.cars.set('44', {
			driver: '44',
			code: null,
			team: null,
			compound: null,
			current: { x: 10, y: 0, tsMs: 1000 },
			previous: { x: 0, y: 0, tsMs: 800 }
		});

		const frame = buildDrawFrame(SQUARE, null, null, state, 900);
		expect(frame.cars[0]?.x).toBeCloseTo(5, 6);
	});

	it('snaps to the latest raw sample under reducedMotion instead of interpolating', () => {
		const state = createRaceState();
		state.cars.set('44', {
			driver: '44',
			code: null,
			team: null,
			compound: null,
			current: { x: 10, y: 0, tsMs: 1000 },
			previous: { x: 0, y: 0, tsMs: 800 }
		});

		const frame = buildDrawFrame(SQUARE, null, null, state, 900, { reducedMotion: true });
		expect(frame.cars[0]?.x).toBe(10);
	});

	it('produces one drawn segment per dominance-filled segment, colored by that driver’s team', () => {
		const arcTable = buildArcLengthTable(SQUARE);
		const dominance = new DominanceTracker(arcTable, 4);
		dominance.ingest('44', 0, 0, 0);
		dominance.ingest('44', 1, 0, 1000);
		dominance.ingest('44', 1, 1, 1800);
		const state = raceStateWith('44', 1, 1, 1800, 'Ferrari');

		const frame = buildDrawFrame(SQUARE, arcTable, dominance, state, 1800);

		expect(frame.segments).toHaveLength(1);
		expect(frame.segments[0]?.color).toBe(colorForTeam('Ferrari'));
	});

	it('skips an unfilled segment rather than drawing a colorless one', () => {
		const arcTable = buildArcLengthTable(SQUARE);
		const dominance = new DominanceTracker(arcTable, 4);
		const frame = buildDrawFrame(SQUARE, arcTable, dominance, createRaceState(), 0);
		expect(frame.segments).toEqual([]);
	});
});
