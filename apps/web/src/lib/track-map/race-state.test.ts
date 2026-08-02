import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { RaceStateReducer } from './race-state';
import type { TrackOutline } from './types';

function envelope(kind: string, payload: Record<string, unknown>, offsetMs: number): Envelope {
	return { event_time_utc: new Date(offsetMs).toISOString(), kind, payload };
}

function squareOutline(): TrackOutline {
	return {
		points: [
			[0, 0],
			[1, 0],
			[1, 1],
			[0, 1],
			[0, 0]
		],
		width_m: 1000,
		height_m: 1000,
		source_points: 4
	};
}

describe('RaceStateReducer', () => {
	it('folds car_position envelopes into raceState.cars', () => {
		const reducer = new RaceStateReducer();
		reducer.update([envelope('car_position', { driver: '44', x: 1, y: 2 }, 0)]);

		expect(reducer.raceState.cars.get('44')?.current).toEqual({ x: 1, y: 2, tsMs: 0 });
	});

	it('only processes envelopes newly appended since the last call (cursor)', () => {
		const reducer = new RaceStateReducer();
		const events: Envelope[] = [envelope('car_position', { driver: '44', x: 1, y: 1 }, 0)];
		reducer.update(events);

		events.push(envelope('car_position', { driver: '44', x: 5, y: 5 }, 1000));
		reducer.update(events);

		const marker = reducer.raceState.cars.get('44');
		expect(marker?.current).toEqual({ x: 5, y: 5, tsMs: 1000 });
		expect(marker?.previous).toEqual({ x: 1, y: 1, tsMs: 0 });
	});

	it('has no dominance tracker until an outline is set', () => {
		const reducer = new RaceStateReducer();
		expect(reducer.dominance).toBeNull();

		reducer.update([envelope('car_position', { driver: '44', x: 0, y: 0 }, 0)]);
		expect(reducer.dominance).toBeNull();
	});

	it('feeds car_position samples into the dominance tracker once an outline is set', () => {
		const reducer = new RaceStateReducer();
		reducer.setOutline(squareOutline());

		reducer.update([
			envelope('car_position', { driver: '44', x: 0, y: 0 }, 0),
			envelope('car_position', { driver: '44', x: 1, y: 0 }, 1000),
			envelope('car_position', { driver: '44', x: 1, y: 1 }, 1800)
		]);

		expect(reducer.dominance?.segments.some((s) => s.timeMs !== null)).toBe(true);
	});

	it('attaches the driver’s current compound to dominance records', () => {
		const reducer = new RaceStateReducer();
		reducer.setOutline(squareOutline());

		reducer.update([
			envelope('stint_started', { driver: '44', compound: 'hard' }, 0),
			envelope('car_position', { driver: '44', x: 0, y: 0 }, 0),
			envelope('car_position', { driver: '44', x: 1, y: 0 }, 1000),
			envelope('car_position', { driver: '44', x: 1, y: 1 }, 1800)
		]);

		const filled = reducer.dominance?.segments.find((s) => s.driver === '44');
		expect(filled?.compound).toBe('hard');
	});

	it('reset clears car and dominance state but keeps the outline', () => {
		const reducer = new RaceStateReducer();
		reducer.setOutline(squareOutline());
		reducer.update([envelope('car_position', { driver: '44', x: 0, y: 0 }, 0)]);

		reducer.reset();

		expect(reducer.raceState.cars.size).toBe(0);
		expect(reducer.dominance).not.toBeNull();
		expect(reducer.dominance?.segments.every((s) => s.driver === null)).toBe(true);
	});
});
