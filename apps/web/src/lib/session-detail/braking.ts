/** Pure transform for the session-detail "Braking" section: one driver's
 * per-lap, per-corner brake-application distances (`f1_kernel.braking_points`
 * via `GET /sessions/{key}/telemetry/{driver}`) rolled up into ONE ROW PER
 * CORNER — a braking map of the circuit.
 *
 * Why per corner rather than per fit: the API returns one fitted
 * `BrakingDrift` per (driver, stint, corner), so a driver with three stints
 * and fifteen corners produces ~45 rows, most of them `thin`. That is a
 * table of statistics, not a feature. A fan asks "where does this driver
 * brake, and is it moving?" — which is one row per corner, with the
 * strongest available fit attached to it.
 *
 * Everything here is a pure function over already-fetched, already-gated
 * data: the component decides WHAT is revealed (via `RevealTracker`), this
 * decides what the revealed rows MEAN. No DOM, no fetch — testable
 * standalone like every other `$lib` core in this app. */
import type { components } from '$lib/api/client';

type BrakingPoint = components['schemas']['BrakingPointRowOut'];
type BrakingDrift = components['schemas']['BrakingDriftOut'];

export interface CornerBraking {
	cornerNumber: number;
	cornerLetter: string | null;
	/** Metres before the corner the driver typically gets on the brakes —
	 * a MEDIAN, so one missed detection or one lock-up cannot move it. */
	medianDistanceM: number;
	/** Earliest and latest across the revealed laps: the spread is the
	 * interesting part (a consistent corner vs one they are hunting). */
	minDistanceM: number;
	maxDistanceM: number;
	/** How many laps actually produced a detection here. */
	lapsDetected: number;
	/** Laps where the telemetry was present but no sustained braking was
	 * found — a flat-out corner reads as this, not as missing data. */
	lapsWithoutBraking: number;
	/** The best available fitted trend for this corner, or `null` when no
	 * stint's fit has been revealed yet. Positive = braking EARLIER as the
	 * stint wears on. */
	drift: BrakingDrift | null;
}

function median(values: number[]): number {
	const sorted = [...values].sort((a, b) => a - b);
	const mid = Math.floor(sorted.length / 2);
	if (sorted.length % 2 === 1) return sorted[mid] as number;
	return ((sorted[mid - 1] as number) + (sorted[mid] as number)) / 2;
}

const RELIABILITY_RANK: Record<string, number> = { good: 3, thin: 2, unreliable: 1 };

/** The fit a corner should be judged by when several stints produced one:
 * the most reliable, and among equals the one built from the most laps.
 * Never an average of fits — averaging a `good` fit with an `unreliable`
 * one launders the uncertainty the kernel deliberately reported. */
export function bestDriftForCorner(fits: BrakingDrift[]): BrakingDrift | null {
	let best: BrakingDrift | null = null;
	for (const fit of fits) {
		if (best === null) {
			best = fit;
			continue;
		}
		const rank = RELIABILITY_RANK[fit.reliability] ?? 0;
		const bestRank = RELIABILITY_RANK[best.reliability] ?? 0;
		if (rank > bestRank || (rank === bestRank && fit.n_laps_used > best.n_laps_used)) best = fit;
	}
	return best;
}

/** One row per corner that produced at least `minLaps` real detections,
 * ordered around the lap. Corners the driver never brakes for (or where
 * the telemetry was too thin to tell) are dropped rather than shown as a
 * zero — `f1_kernel.braking_points` distinguishes "no braking found" from
 * "not enough telemetry to judge", and neither is a braking point. */
export function buildCornerBraking(
	points: BrakingPoint[],
	drifts: BrakingDrift[],
	minLaps = 3
): CornerBraking[] {
	const byCorner = new Map<number, { detected: number[]; letter: string | null; empty: number }>();
	for (const row of points) {
		const entry = byCorner.get(row.corner_number) ?? {
			detected: [],
			letter: row.corner_letter,
			empty: 0
		};
		if (row.distance_before_corner_m !== null) entry.detected.push(row.distance_before_corner_m);
		else if (row.known) entry.empty += 1;
		byCorner.set(row.corner_number, entry);
	}

	const driftsByCorner = new Map<number, BrakingDrift[]>();
	for (const fit of drifts) {
		const list = driftsByCorner.get(fit.corner_number) ?? [];
		list.push(fit);
		driftsByCorner.set(fit.corner_number, list);
	}

	const rows: CornerBraking[] = [];
	for (const [cornerNumber, entry] of byCorner) {
		if (entry.detected.length < minLaps) continue;
		rows.push({
			cornerNumber,
			cornerLetter: entry.letter,
			medianDistanceM: median(entry.detected),
			minDistanceM: Math.min(...entry.detected),
			maxDistanceM: Math.max(...entry.detected),
			lapsDetected: entry.detected.length,
			lapsWithoutBraking: entry.empty,
			drift: bestDriftForCorner(driftsByCorner.get(cornerNumber) ?? [])
		});
	}
	return rows.sort((a, b) => a.cornerNumber - b.cornerNumber);
}

export type DriftDirection = 'earlier' | 'later' | 'steady' | 'unknown';

/** How to read a corner's trend in words.
 *
 * "Steady" is anything inside the fit's own 95% confidence interval
 * crossing zero — i.e. the kernel cannot tell the direction apart from
 * noise. Calling that "earlier" because the point estimate happens to be
 * positive would be exactly the false precision CLAUDE.md's confidence
 * contract exists to prevent. */
export function driftDirection(drift: BrakingDrift | null): DriftDirection {
	if (drift === null) return 'unknown';
	if (drift.reliability === 'unreliable') return 'unknown';
	if (drift.drift_ci_low_m_per_lap <= 0 && drift.drift_ci_high_m_per_lap >= 0) return 'steady';
	return drift.drift_m_per_lap > 0 ? 'earlier' : 'later';
}

export const DRIFT_LABEL: Record<DriftDirection, string> = {
	earlier: 'braking earlier',
	later: 'braking later',
	steady: 'holding steady',
	unknown: 'not enough evidence'
};

/** The corner where this driver's braking point is moving most — the one
 * worth leading with. `null` when no corner has a directional trend. */
export function headlineCorner(rows: CornerBraking[]): CornerBraking | null {
	let best: CornerBraking | null = null;
	for (const row of rows) {
		const direction = driftDirection(row.drift);
		if (direction !== 'earlier' && direction !== 'later') continue;
		const magnitude = Math.abs(row.drift?.drift_m_per_lap ?? 0);
		const bestMagnitude = Math.abs(best?.drift?.drift_m_per_lap ?? 0);
		if (best === null || magnitude > bestMagnitude) best = row;
	}
	return best;
}
