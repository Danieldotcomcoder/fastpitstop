import { describe, expect, it } from 'vitest';
import { buildArcLengthTable, pointAtArcLength, projectToArcLength } from './arc-length';
import type { Point } from './types';

function square(): Point[] {
	return [
		[0, 0],
		[10, 0],
		[10, 10],
		[0, 10],
		[0, 0]
	];
}

describe('buildArcLengthTable', () => {
	it('rejects a table with fewer than two points', () => {
		expect(() => buildArcLengthTable([[0, 0]])).toThrow(/at least two/);
	});

	it('accumulates total perimeter correctly', () => {
		const table = buildArcLengthTable(square());
		expect(table.totalLength).toBeCloseTo(40, 6);
		expect(table.cumulative[0]).toBe(0);
		expect(table.cumulative.at(-1)).toBeCloseTo(40, 6);
	});
});

describe('projectToArcLength', () => {
	it('returns 0 at the start point', () => {
		const table = buildArcLengthTable(square());
		expect(projectToArcLength(table, 0, 0)).toBeCloseTo(0, 6);
	});

	it('returns the fraction of the way around for a point on a later edge', () => {
		const table = buildArcLengthTable(square());
		// (10, 10) is the third vertex, 20 of 40 units around -> 0.5.
		expect(projectToArcLength(table, 10, 10)).toBeCloseTo(0.5, 6);
	});

	it('snaps an off-outline point to its nearest vertex', () => {
		const table = buildArcLengthTable(square());
		expect(projectToArcLength(table, 10.4, -0.2)).toBeCloseTo(0.25, 6);
	});
});

describe('pointAtArcLength', () => {
	it('round-trips with projectToArcLength at exact vertices', () => {
		const table = buildArcLengthTable(square());
		const [x, y] = pointAtArcLength(table, 0.25);
		expect(x).toBeCloseTo(10, 6);
		expect(y).toBeCloseTo(0, 6);
	});

	it('wraps fractions outside [0, 1)', () => {
		const table = buildArcLengthTable(square());
		const wrapped = pointAtArcLength(table, 1.25);
		const base = pointAtArcLength(table, 0.25);
		expect(wrapped[0]).toBeCloseTo(base[0], 6);
		expect(wrapped[1]).toBeCloseTo(base[1], 6);
	});

	it('interpolates midway along a straight edge', () => {
		const table = buildArcLengthTable(square());
		const [x, y] = pointAtArcLength(table, 0.125);
		expect(x).toBeCloseTo(5, 6);
		expect(y).toBeCloseTo(0, 6);
	});
});
