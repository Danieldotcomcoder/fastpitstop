import { describe, expect, it } from 'vitest';
import { fitOutlineToCanvas, outlineFromApi, project } from './geometry';
import type { Point } from './types';

describe('fitOutlineToCanvas', () => {
	it('centers a square outline in a square canvas', () => {
		const points: Point[] = [
			[-1, -1],
			[1, -1],
			[1, 1],
			[-1, 1]
		];
		const viewport = fitOutlineToCanvas(points, 200, 200, 0);
		const [x, y] = project(viewport, [0, 0]);
		expect(x).toBeCloseTo(100, 6);
		expect(y).toBeCloseTo(100, 6);
	});

	it('never stretches a non-square shape to fill a square canvas', () => {
		// A 2:1 wide rectangle in a square canvas must scale by the SAME
		// factor on both axes — the real shape's aspect ratio must survive.
		const points: Point[] = [
			[-2, -1],
			[2, -1],
			[2, 1],
			[-2, 1]
		];
		const viewport = fitOutlineToCanvas(points, 400, 400, 0);
		const left = project(viewport, [-2, 0]);
		const right = project(viewport, [2, 0]);
		const top = project(viewport, [0, -1]);
		const bottom = project(viewport, [0, 1]);
		const renderedWidth = right[0] - left[0];
		const renderedHeight = bottom[1] - top[1];
		// The shape spans 4 units wide by 2 units tall (a 2:1 ratio) — the
		// rendered proportions must match, not the canvas's own 1:1 shape.
		expect(renderedWidth / renderedHeight).toBeCloseTo(2, 6);
	});

	it('respects padding by leaving margin on the tighter axis', () => {
		const points: Point[] = [
			[0, 0],
			[100, 100]
		];
		const viewport = fitOutlineToCanvas(points, 200, 200, 20);
		const [x0] = project(viewport, [0, 0]);
		expect(x0).toBeGreaterThanOrEqual(20 - 1e-6);
	});

	it('degrades to a stable identity viewport on an empty outline', () => {
		const viewport = fitOutlineToCanvas([], 300, 150);
		expect(viewport.scale).toBeGreaterThan(0);
		expect(Number.isFinite(viewport.offsetX)).toBe(true);
		expect(Number.isFinite(viewport.offsetY)).toBe(true);
	});
});

describe('outlineFromApi', () => {
	it('returns null for a null/undefined response (no shipped outline yet)', () => {
		expect(outlineFromApi(null)).toBeNull();
		expect(outlineFromApi(undefined)).toBeNull();
	});

	it('converts raw point tuples into real 2-tuples, preserving metadata', () => {
		const outline = outlineFromApi({
			points: [
				[0, 0],
				[1, 2]
			],
			width_m: 100,
			height_m: 50,
			source_points: 2
		});
		expect(outline).toEqual({
			points: [
				[0, 0],
				[1, 2]
			],
			width_m: 100,
			height_m: 50,
			source_points: 2
		});
	});

	it('drops a malformed point rather than trusting the network response blindly', () => {
		const outline = outlineFromApi({
			points: [[0, 0], [Number.NaN] as unknown as number[], [1, 1]],
			width_m: 10,
			height_m: 10,
			source_points: 3
		});
		expect(outline?.points).toEqual([
			[0, 0],
			[1, 1]
		]);
	});
});
