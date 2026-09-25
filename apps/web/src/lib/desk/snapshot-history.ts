/** The pit board's sparkline: the focused pair's interval against the
 * "undercut line" over the last laps.
 *
 * The line is the interval at which stopping would exactly break even. The
 * kernel's margin is linear in the starting gap (a second more interval is
 * a second less margin), so the line is simply `interval + margin`; below
 * it, the stop pays. Both come from each lap's own `kernel_snapshot`, which
 * Delay Sync only ever releases on time, so this history is spoiler-safe
 * by construction. Only the latest snapshot survives in `VisibleSessionState`,
 * hence this small accumulator. */
import type { Crossover } from './pit-board';

export interface SparkPoint {
	lap: number;
	gapS: number | null;
	lineS: number | null;
}

export class SnapshotHistory {
	/** Keyed by `attacker>defender`, then by decision lap. */
	private readonly byPair = new Map<string, Map<number, SparkPoint>>();
	private lastSeen: unknown = null;

	/** Fold one released snapshot's crossovers in. Idempotent per snapshot. */
	record(
		snapshot: { crossovers?: readonly unknown[] } | null,
		isCrossover: (v: unknown) => v is Crossover
	): void {
		if (snapshot === null || snapshot === this.lastSeen) return;
		this.lastSeen = snapshot;
		for (const value of snapshot.crossovers ?? []) {
			if (!isCrossover(value) || value.kind !== 'undercut') continue;
			const key = `${value.attacker}>${value.defender}`;
			let laps = this.byPair.get(key);
			if (!laps) {
				laps = new Map();
				this.byPair.set(key, laps);
			}
			const gap = value.gap_s ?? null;
			const margin = value.projected_gap_if_covered_s ?? null;
			laps.set(value.decision_lap, {
				lap: value.decision_lap,
				gapS: gap,
				lineS: gap !== null && margin !== null ? gap + margin : null
			});
		}
	}

	/** Up to `span` most recent laps for this pair, oldest first. */
	points(attacker: string, defender: string, span = 12): SparkPoint[] {
		const laps = this.byPair.get(`${attacker}>${defender}`);
		if (!laps) return [];
		return [...laps.values()].sort((x, y) => x.lap - y.lap).slice(-span);
	}

	reset(): void {
		this.byPair.clear();
		this.lastSeen = null;
	}
}
