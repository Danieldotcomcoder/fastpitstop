import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { MeasuredPositions } from './measured';

const T0 = Date.UTC(2026, 8, 13, 13, 3);

function frame(offsetMs: number, cars: Record<string, [number, number]>): Envelope {
	return {
		event_time_utc: new Date(T0 + offsetMs).toISOString(),
		kind: 'car_positions',
		payload: { cars }
	};
}

function window(): Envelope[] {
	return [
		frame(0, { '1': [0, 0], '44': [100, 0] }),
		frame(250, { '1': [10, 0], '44': [110, 0] }),
		frame(500, { '1': [20, 0], '44': [120, 0] })
	];
}

describe('MeasuredPositions', () => {
	it('interpolates between the samples either side of the drawn instant', () => {
		const positions = new MeasuredPositions();
		positions.update(window());

		expect(positions.at('1', T0 + 125)).toEqual({ x: 5, y: 0, holdMs: 0, behindFeedMs: 0 });
		expect(positions.at('44', T0 + 400)).toEqual({ x: 116, y: 0, holdMs: 0, behindFeedMs: 0 });
		expect(positions.at('1', T0 + 250)).toEqual({ x: 10, y: 0, holdMs: 0, behindFeedMs: 0 });
	});

	it('holds at the newest sample instead of extrapolating past it', () => {
		const positions = new MeasuredPositions();
		positions.update(window());

		expect(positions.at('1', T0 + 900)).toEqual({ x: 20, y: 0, holdMs: 400, behindFeedMs: 0 });
	});

	it('tells a car the feed stopped placing from a feed that stopped', () => {
		const positions = new MeasuredPositions();
		positions.update([
			frame(0, { '1': [0, 0], '44': [100, 0] }),
			frame(250, { '44': [110, 0] }),
			frame(500, { '44': [120, 0] })
		]);

		// 44 is still being placed, 1 is not: 1 trails the feed.
		expect(positions.at('1', T0 + 600)?.behindFeedMs).toBe(500);
		expect(positions.at('44', T0 + 600)?.behindFeedMs).toBe(0);
		// Long after the last frame, the feed itself has stopped: nobody trails it.
		expect(positions.at('44', T0 + 60_000)?.behindFeedMs).toBe(0);
	});

	it('holds at the oldest sample before the window starts', () => {
		const positions = new MeasuredPositions();
		positions.update(window());

		expect(positions.at('1', T0 - 1000)).toEqual({ x: 0, y: 0, holdMs: 0, behindFeedMs: 0 });
	});

	it('knows nothing of a car no frame placed', () => {
		const positions = new MeasuredPositions();
		positions.update(window());

		expect(positions.at('81', T0 + 100)).toBeNull();
		expect(new MeasuredPositions().hasPositions).toBe(false);
		expect(positions.hasPositions).toBe(true);
	});

	it('follows a car the feed skipped in one frame across the gap', () => {
		const positions = new MeasuredPositions();
		positions.update([
			frame(0, { '1': [0, 0] }),
			frame(250, { '44': [0, 0] }),
			frame(500, { '1': [20, 0] })
		]);

		expect(positions.at('1', T0 + 250)).toEqual({ x: 10, y: 0, holdMs: 0, behindFeedMs: 0 });
	});

	it('copes with a late frame released after a newer one', () => {
		const positions = new MeasuredPositions();
		const [a, b, c] = window();
		positions.update([a!, c!, b!]);

		expect(positions.at('1', T0 + 375)).toEqual({ x: 15, y: 0, holdMs: 0, behindFeedMs: 0 });
	});

	it('skips malformed cars and frames without failing the rest', () => {
		const positions = new MeasuredPositions();
		positions.update([
			{ event_time_utc: new Date(T0).toISOString(), kind: 'car_positions', payload: {} },
			{
				event_time_utc: new Date(T0 + 250).toISOString(),
				kind: 'car_positions',
				payload: { cars: { '1': [5, 6], '2': ['x', 1], '3': 7 } }
			}
		]);

		expect(positions.at('1', T0 + 250)).toEqual({ x: 5, y: 6, holdMs: 0, behindFeedMs: 0 });
		expect(positions.at('2', T0 + 250)).toBeNull();
		expect(positions.at('3', T0 + 250)).toBeNull();
	});

	it('draws a fixed distance behind the clock for a given speed', () => {
		expect(MeasuredPositions.lagMs(100, 1)).toBe(280 * 1.5 + 100 + 200);
		expect(MeasuredPositions.lagMs(100, 16)).toBeCloseTo((280 * 1.5) / 16 + 300);
		expect(MeasuredPositions.lagMs(100, 16)).toBeLessThan(MeasuredPositions.lagMs(100, 1));
	});

	it('holds across a gap instead of gliding between far-apart samples', () => {
		const positions = new MeasuredPositions();
		positions.update([frame(0, { '1': [0, 0] }), frame(5000, { '1': [1000, 0] })]);

		expect(positions.at('1', T0 + 2500, 2000)).toMatchObject({ x: 0, y: 0 });
		expect(positions.at('1', T0 + 2500)).toMatchObject({ x: 500, y: 0 });
	});

	it('treats a new window as the whole truth', () => {
		const positions = new MeasuredPositions();
		positions.update(window());
		positions.update([frame(1000, { '44': [1, 1] })]);

		expect(positions.at('1', T0 + 1000)).toBeNull();
		positions.reset();
		expect(positions.hasPositions).toBe(false);
	});

	it('smooths jittered timestamps into steady motion', () => {
		// A car moving 10 units every 250 ms, but its timestamps jittered.
		const jitter = [0, 60, -50, 40, -60, 50, -40, 0, 30, -30, 0];
		const frames = jitter.map((j, i) => frame(i * 250 + j, { '1': [i * 10, 0] }));
		const positions = new MeasuredPositions();
		positions.update(frames);

		const raw: number[] = [];
		const smooth: number[] = [];
		for (let ms = 750; ms <= 1750; ms += 50) {
			raw.push(positions.at('1', T0 + ms)!.x);
			smooth.push(positions.at('1', T0 + ms, Infinity, 250)!.x);
		}
		const speeds = (xs: number[]) => xs.slice(1).map((x, i) => x - xs[i]!);
		const spread = (v: number[]) => Math.max(...v) - Math.min(...v);
		// True speed is 2 units per 50 ms; smoothing keeps it close to that.
		expect(spread(speeds(smooth))).toBeLessThan(spread(speeds(raw)) / 2);
		expect(Math.min(...speeds(smooth))).toBeGreaterThan(0);
	});

	it('never extrapolates past the newest sample when smoothing', () => {
		const positions = new MeasuredPositions();
		positions.update(window());

		const at = positions.at('1', T0 + 5000, Infinity, 250)!;
		expect(at.x).toBeLessThanOrEqual(20.5);
		expect(at.holdMs).toBe(4500);
	});

	it('does not smooth across a gap', () => {
		const positions = new MeasuredPositions();
		positions.update([
			frame(0, { '1': [0, 0] }),
			frame(250, { '1': [10, 0] }),
			frame(5000, { '1': [1000, 0] })
		]);

		expect(positions.at('1', T0 + 2500, 2000, 250)).toMatchObject({ x: 10, y: 0 });
	});
});
