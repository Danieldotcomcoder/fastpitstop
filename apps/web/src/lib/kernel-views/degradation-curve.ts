/** Thin `DegradationFitOut`-shaped wrapper over `linear-fit.ts`'s generic
 * curve sampler/crossover finder — keeps the generic module free of any
 * f1_api-specific field names. */
import { findCrossover, sampleCurve, type CurveSeries } from './linear-fit';

export interface DegradationFitLike {
	driver: string;
	stint_number: number;
	compound: string;
	intercept_s: number;
	deg_rate_s_per_lap: number;
	deg_rate_ci_low_s_per_lap: number;
	deg_rate_ci_high_s_per_lap: number;
	quadratic_term_s_per_lap2: number | null;
	n_laps_used: number;
	reliability: 'good' | 'thin' | 'unreliable';
}

export function degradationCurve(fit: DegradationFitLike, maxAge: number, steps = 20): CurveSeries {
	return sampleCurve(
		fit.intercept_s,
		fit.deg_rate_s_per_lap,
		fit.deg_rate_ci_low_s_per_lap,
		fit.deg_rate_ci_high_s_per_lap,
		maxAge,
		fit.quadratic_term_s_per_lap2 ?? 0,
		steps
	);
}

export function degradationCrossover(
	a: DegradationFitLike,
	b: DegradationFitLike,
	maxAge: number
): { age: number; value: number } | null {
	return findCrossover(
		{
			intercept: a.intercept_s,
			rate: a.deg_rate_s_per_lap,
			quadratic: a.quadratic_term_s_per_lap2 ?? 0
		},
		{
			intercept: b.intercept_s,
			rate: b.deg_rate_s_per_lap,
			quadratic: b.quadratic_term_s_per_lap2 ?? 0
		},
		maxAge
	);
}
