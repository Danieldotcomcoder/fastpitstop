import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { LapProgressTracker, outlineTable, syntheticPositions } from './synthetic-positions';
import type { TrackOutline } from './types';

const T0 = Date.parse('2026-09-13T13:30:00.000Z');

const env = (kind: string, payload: Record<string, unknown>, offsetMs = 0): Envelope => ({
	event_time_utc: new Date(T0 + offsetMs).toISOString(),
	kind,
	payload
});

/** A unit square loop: four corners, so a fraction maps to an obvious side. */
const SQUARE: TrackOutline = {
	points: [
		[0, 0],
		[100, 0],
		[100, 100],
		[0, 100],
		[0, 0]
	],
	width_m: 100,
	height_m: 100,
	source_points: 5
};

const lap = (driver: string, seconds: number, offsetMs: number, lapNumber = 1): Envelope =>
	env('lap_completed', { driver, lap_number: lapNumber, lap_time: `PT${seconds}S` }, offsetMs);

describe('LapProgressTracker', () => {
	it('has no opinion before a driver has completed a lap', () => {
		const t = new LapProgressTracker();
		t.update([env('gap_updated', { driver: '1' })]);
		expect(t.fractionAt('1', T0 + 10_000)).toBeNull();
	});

	it('places a driver by how long ago they crossed the line', () => {
		const t = new LapProgressTracker();
		t.update([lap('1', 100, 0)]);
		expect(t.fractionAt('1', T0)).toBeCloseTo(0, 5);
		expect(t.fractionAt('1', T0 + 25_000)).toBeCloseTo(0.25, 5);
		expect(t.fractionAt('1', T0 + 50_000)).toBeCloseTo(0.5, 5);
	});

	it('parks at the line rather than inventing a lap that never happened', () => {
		const t = new LapProgressTracker();
		t.update([lap('1', 100, 0)]);
		// 3x the lap time later with no new lap_completed: our estimate has
		// expired. Wrapping would show the car starting laps it never ran.
		expect(t.fractionAt('1', T0 + 300_000)).toBeCloseTo(0.999, 3);
	});

	it('restarts the lap when the driver crosses the line again', () => {
		// One GROWING log, the way `visibleState.events` behaves — the
		// cursor reads from where it stopped, so a caller must append.
		const t = new LapProgressTracker();
		const log = [lap('1', 100, 0)];
		t.update(log);
		log.push(lap('1', 100, 100_000, 2));
		t.update(log);
		expect(t.fractionAt('1', T0 + 100_000)).toBeCloseTo(0, 5);
		expect(t.fractionAt('1', T0 + 150_000)).toBeCloseTo(0.5, 5);
	});

	it('ignores implausible lap times and keeps the last good pace', () => {
		const t = new LapProgressTracker();
		const log = [lap('1', 100, 0)];
		t.update(log);
		// A safety-car crawl: the crossing still resets the lap, but a
		// 600s "lap time" must not stretch the car around at the wrong rate.
		log.push(lap('1', 600, 100_000, 2));
		t.update(log);
		expect(t.fractionAt('1', T0 + 150_000)).toBeCloseTo(0.5, 5);
	});

	it('tracks pit occupancy from entry and exit', () => {
		const t = new LapProgressTracker();
		const log = [lap('1', 100, 0), env('pit_entry', { driver: '1' }, 10_000)];
		t.update(log);
		expect(t.inPit('1')).toBe(true);
		log.push(env('pit_exit', { driver: '1' }, 40_000));
		t.update(log);
		expect(t.inPit('1')).toBe(false);
	});

	it('folds incrementally without double counting', () => {
		const t = new LapProgressTracker();
		const events = [lap('1', 100, 0)];
		t.update(events);
		t.update(events); // same array, already consumed
		expect(t.fractionAt('1', T0 + 50_000)).toBeCloseTo(0.5, 5);
	});
});

describe('syntheticPositions', () => {
	it('puts cars at different points of the same lap, in order', () => {
		const t = new LapProgressTracker();
		t.update([lap('1', 100, 0), lap('16', 100, -20_000)]);
		const table = outlineTable(SQUARE);
		const at = syntheticPositions(t, table, T0 + 10_000);

		const leader = at.get('1');
		const chaser = at.get('16');
		expect(leader).toBeDefined();
		expect(chaser).toBeDefined();
		// Driver 1 crossed 10s ago (10% round); driver 16 crossed 30s ago
		// (30% round) — further along, i.e. a different point on track.
		expect(leader).not.toEqual(chaser);
	});

	it('skips drivers it cannot place rather than putting them at the start line', () => {
		const t = new LapProgressTracker();
		t.update([lap('1', 100, 0), env('gap_updated', { driver: '99' })]);
		const at = syntheticPositions(t, outlineTable(SQUARE), T0 + 10_000);
		expect(at.has('1')).toBe(true);
		expect(at.has('99')).toBe(false);
	});

	it('produces points that lie on the outline', () => {
		const t = new LapProgressTracker();
		t.update([lap('1', 100, 0)]);
		const sample = syntheticPositions(t, outlineTable(SQUARE), T0 + 50_000).get('1');
		expect(sample).toBeDefined();
		// On the square's perimeter: one coordinate pinned to an edge.
		const onEdge = sample!.x === 0 || sample!.x === 100 || sample!.y === 0 || sample!.y === 100;
		expect(onEdge).toBe(true);
	});
});
