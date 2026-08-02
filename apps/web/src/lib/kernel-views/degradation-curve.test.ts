import { describe, expect, it } from 'vitest';
import {
	degradationCrossover,
	degradationCurve,
	type DegradationFitLike
} from './degradation-curve';

function fit(overrides: Partial<DegradationFitLike> = {}): DegradationFitLike {
	return {
		driver: '44',
		stint_number: 1,
		compound: 'medium',
		intercept_s: 90,
		deg_rate_s_per_lap: 0.1,
		deg_rate_ci_low_s_per_lap: 0.05,
		deg_rate_ci_high_s_per_lap: 0.15,
		quadratic_term_s_per_lap2: null,
		n_laps_used: 12,
		reliability: 'good',
		...overrides
	};
}

describe('degradationCurve', () => {
	it('starts at the fit intercept', () => {
		const curve = degradationCurve(fit(), 12);
		expect(curve.central[0]).toBeCloseTo(90, 6);
	});

	it('applies the quadratic term when present', () => {
		const withQuad = degradationCurve(fit({ quadratic_term_s_per_lap2: 0.01 }), 10, 1);
		const linear = degradationCurve(fit(), 10, 1);
		expect(withQuad.central[1]).toBeGreaterThan(linear.central[1]!);
	});
});

describe('degradationCrossover', () => {
	it('finds a crossover between two different fits', () => {
		const a = fit({ driver: '44', intercept_s: 90, deg_rate_s_per_lap: 0.2 });
		const b = fit({ driver: '1', intercept_s: 91, deg_rate_s_per_lap: 0.05 });
		const crossover = degradationCrossover(a, b, 20);
		expect(crossover).not.toBeNull();
	});

	it('returns null for parallel fits', () => {
		const a = fit({ intercept_s: 90, deg_rate_s_per_lap: 0.1 });
		const b = fit({ intercept_s: 92, deg_rate_s_per_lap: 0.1 });
		expect(degradationCrossover(a, b, 20)).toBeNull();
	});
});
