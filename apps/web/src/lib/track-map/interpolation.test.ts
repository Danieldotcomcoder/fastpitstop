import { describe, expect, it } from 'vitest';
import { DEFAULT_STALE_AFTER_MS, interpolatePosition } from './interpolation';
import type { PositionSample } from './types';

describe('interpolatePosition', () => {
	it('returns null when there is no current sample yet', () => {
		expect(interpolatePosition(null, null, 1000)).toBeNull();
	});

	it('returns the single known point when there is no previous sample', () => {
		const current: PositionSample = { x: 5, y: 7, tsMs: 1000 };
		const result = interpolatePosition(current, null, 1000);
		expect(result).toEqual({ x: 5, y: 7, stale: false });
	});

	it('lerps halfway between previous and current at the midpoint time', () => {
		const previous: PositionSample = { x: 0, y: 0, tsMs: 1000 };
		const current: PositionSample = { x: 10, y: 20, tsMs: 1200 };
		const result = interpolatePosition(current, previous, 1100);
		expect(result?.x).toBeCloseTo(5, 6);
		expect(result?.y).toBeCloseTo(10, 6);
		expect(result?.stale).toBe(false);
	});

	it('clamps to the current sample once the render clock passes it', () => {
		const previous: PositionSample = { x: 0, y: 0, tsMs: 1000 };
		const current: PositionSample = { x: 10, y: 20, tsMs: 1200 };
		const result = interpolatePosition(current, previous, 1500);
		expect(result?.x).toBeCloseTo(10, 6);
		expect(result?.y).toBeCloseTo(20, 6);
	});

	it('clamps to the previous sample when the render clock is somehow earlier', () => {
		const previous: PositionSample = { x: 0, y: 0, tsMs: 1000 };
		const current: PositionSample = { x: 10, y: 20, tsMs: 1200 };
		const result = interpolatePosition(current, previous, 900);
		expect(result?.x).toBeCloseTo(0, 6);
		expect(result?.y).toBeCloseTo(0, 6);
	});

	it('flags stale once the hold exceeds the threshold', () => {
		const current: PositionSample = { x: 5, y: 7, tsMs: 1000 };
		const fresh = interpolatePosition(current, null, 1000 + DEFAULT_STALE_AFTER_MS - 1);
		const stale = interpolatePosition(current, null, 1000 + DEFAULT_STALE_AFTER_MS + 1);
		expect(fresh?.stale).toBe(false);
		expect(stale?.stale).toBe(true);
	});

	it('handles a zero-length sample gap without dividing by zero', () => {
		const sample: PositionSample = { x: 3, y: 4, tsMs: 1000 };
		const result = interpolatePosition(sample, { ...sample }, 1000);
		expect(result).toEqual({ x: 3, y: 4, stale: false });
	});
});
