import { describe, expect, it } from 'vitest';
import { evalFit, findCrossover, sampleCurve } from './linear-fit';

describe('evalFit', () => {
	it('evaluates a plain linear fit', () => {
		expect(evalFit(90, 0.1, 10)).toBeCloseTo(91, 6);
	});

	it('includes the quadratic term when given', () => {
		expect(evalFit(90, 0.1, 10, 0.01)).toBeCloseTo(91 + 0.01 * 100, 6);
	});
});

describe('sampleCurve', () => {
	it('starts every series at the shared intercept when age is 0', () => {
		const curve = sampleCurve(90, 0.1, 0.05, 0.15, 10);
		expect(curve.central[0]).toBeCloseTo(90, 6);
		expect(curve.low[0]).toBeCloseTo(90, 6);
		expect(curve.high[0]).toBeCloseTo(90, 6);
	});

	it('widens the CI band with tyre age', () => {
		const curve = sampleCurve(90, 0.1, 0.05, 0.15, 10, 0, 10);
		const lastIndex = curve.age.length - 1;
		const bandAtStart = curve.high[0]! - curve.low[0]!;
		const bandAtEnd = curve.high[lastIndex]! - curve.low[lastIndex]!;
		expect(bandAtEnd).toBeGreaterThan(bandAtStart);
	});

	it('samples steps + 1 points from 0 to maxAge', () => {
		const curve = sampleCurve(90, 0.1, 0.05, 0.15, 20, 0, 4);
		expect(curve.age).toEqual([0, 5, 10, 15, 20]);
	});
});

describe('findCrossover', () => {
	it('finds where two converging lines cross', () => {
		// A starts faster (lower) but degrades quicker; B starts slower but holds better.
		const a = { intercept: 90, rate: 0.2 };
		const b = { intercept: 91, rate: 0.05 };
		const crossover = findCrossover(a, b, 20);
		expect(crossover).not.toBeNull();
		expect(crossover?.age).toBeCloseTo(1 / 0.15, 1);
	});

	it('returns null when the lines never cross in range', () => {
		const a = { intercept: 90, rate: 0.05 };
		const b = { intercept: 95, rate: 0.05 };
		expect(findCrossover(a, b, 20)).toBeNull();
	});

	it('returns the shared start when the two fits already share an intercept', () => {
		const a = { intercept: 90, rate: 0.1 };
		const b = { intercept: 90, rate: 0.2 };
		const crossover = findCrossover(a, b, 20);
		expect(crossover?.age).toBe(0);
		expect(crossover?.value).toBeCloseTo(90, 6);
	});
});
