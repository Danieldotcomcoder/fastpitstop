/** Shared evaluator for the kernel's fitted (Theil-Sen, optionally
 * Huber-quadratic) lines — `DegradationFit`'s pace-vs-tyre-age and
 * `BrakingDrift`'s distance-vs-tyre-age both have the exact same shape
 * (intercept + rate * age [+ quadratic * age^2], plus a 95% CI on the
 * rate). This only ever EVALUATES an already-fitted curve at a given x and
 * finds where two of them cross — presentation, not analysis, per
 * CLAUDE.md's "no analytics in the frontend" (the fitting itself stays the
 * kernel's job). */

export function evalFit(intercept: number, rate: number, x: number, quadratic = 0): number {
	return intercept + rate * x + quadratic * x * x;
}

export interface CurveSeries {
	age: number[];
	central: number[];
	low: number[];
	high: number[];
}

/** Samples a fit across `steps` even points over `[0, maxAge]`. The CI band
 * fans out from the SAME intercept at age 0 using the rate's own 95% CI —
 * the kernel only publishes a confidence interval on the rate, never on the
 * intercept, so this is the honest way to draw uncertainty that widens with
 * tyre age rather than inventing an intercept CI the fit never reported. */
export function sampleCurve(
	intercept: number,
	rate: number,
	rateCiLow: number,
	rateCiHigh: number,
	maxAge: number,
	quadratic = 0,
	steps = 20
): CurveSeries {
	const age: number[] = [];
	const central: number[] = [];
	const low: number[] = [];
	const high: number[] = [];
	const safeMaxAge = Math.max(maxAge, 1);
	for (let i = 0; i <= steps; i++) {
		const x = (safeMaxAge * i) / steps;
		age.push(x);
		central.push(evalFit(intercept, rate, x, quadratic));
		low.push(evalFit(intercept, rateCiLow, x, quadratic));
		high.push(evalFit(intercept, rateCiHigh, x, quadratic));
	}
	return { age, central, low, high };
}

export interface FitLine {
	intercept: number;
	rate: number;
	quadratic?: number;
}

export interface Crossover {
	age: number;
	value: number;
}

/** Where two fitted lines/curves cross within `[0, maxAge]`: sample both,
 * locate the first sign change of `(A - B)`, and refine with one linear
 * interpolation step — good enough at plotting resolution and far simpler
 * than solving the quadratic directly (the Huber quadratic term is rare and
 * small; a sampled crossing is indistinguishable from an exact one on
 * screen). `null` when the two never cross inside the range. */
export function findCrossover(
	fitA: FitLine,
	fitB: FitLine,
	maxAge: number,
	steps = 200
): Crossover | null {
	const at = (fit: FitLine, x: number): number =>
		evalFit(fit.intercept, fit.rate, x, fit.quadratic ?? 0);
	let prevX = 0;
	let prevDiff = at(fitA, 0) - at(fitB, 0);
	if (prevDiff === 0) return { age: 0, value: at(fitA, 0) };

	for (let i = 1; i <= steps; i++) {
		const x = (maxAge * i) / steps;
		const diff = at(fitA, x) - at(fitB, x);
		if (diff === 0) return { age: x, value: at(fitA, x) };
		if ((prevDiff < 0 && diff > 0) || (prevDiff > 0 && diff < 0)) {
			const t = prevDiff / (prevDiff - diff);
			const age = prevX + (x - prevX) * t;
			return { age, value: at(fitA, age) };
		}
		prevX = x;
		prevDiff = diff;
	}
	return null;
}
