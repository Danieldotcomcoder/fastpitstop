/** Pure helpers for `<Estimate>` — band geometry from CI + sample support. */

export const MIN_CLEAN_SAMPLES = 4;

/** Bright, tight bands once we have ~40 clean laps of support. */
export const FULL_SUPPORT_SAMPLES = 40;

export function hasEnoughSamples(n: number, min = MIN_CLEAN_SAMPLES): boolean {
	return Number.isFinite(n) && n >= min;
}

/** Opacity of the reliability hairline: faint at the floor, bright at full support. */
export function bandOpacity(sampleSize: number, min = MIN_CLEAN_SAMPLES): number {
	if (!hasEnoughSamples(sampleSize, min)) return 0;
	const t = (sampleSize - min) / (FULL_SUPPORT_SAMPLES - min);
	return Math.min(1, Math.max(0.28, t));
}

/**
 * Relative band width in [0, 1]: wider = less certain.
 * `ciHalfWidth` is half the interval; `referenceScale` is a typical magnitude
 * for the quantity (e.g. |point| or a domain default) so a ±0.04s/lap band
 * on a 0.05 rate reads tight, and a ±0.4 band reads wide.
 */
export function bandWidthFraction(
	ciLow: number | null | undefined,
	ciHigh: number | null | undefined,
	referenceScale: number
): number {
	if (
		ciLow == null ||
		ciHigh == null ||
		!Number.isFinite(ciLow) ||
		!Number.isFinite(ciHigh)
	) {
		return 0.55;
	}
	const half = Math.abs(ciHigh - ciLow) / 2;
	const scale = Math.max(Math.abs(referenceScale), half, 1e-6);
	return Math.min(1, Math.max(0.08, half / scale));
}

export function defaultFormat(value: number, digits = 2): string {
	return value.toFixed(digits);
}

export function formatSigned(value: number, digits = 2): string {
	const body = Math.abs(value).toFixed(digits);
	if (value > 0) return `+${body}`;
	if (value < 0) return `−${body}`;
	return body;
}
