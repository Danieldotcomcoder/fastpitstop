/** Where each car sits on the lap, when the feed does not say.
 *
 * The leader is placed from its own lap timing: how far round it is since
 * it last crossed the line, at the pace of its last lap. Every other car is
 * placed BEHIND the leader by its gap on the timing screen, converted to lap
 * distance at the leader's pace.
 *
 * Why not place every car from its own line crossings: on a race ingested
 * live while the ingest lagged, crossings arrive in bursts, not one per lap.
 * Most cars then went 1.5-3 laps without a recorded crossing, overshot, and
 * piled up at the start/finish line (the 2026 Spanish GP replay). Gap
 * updates arrive continuously, so spacing built from them matches the
 * timing tower exactly and the running order can never disagree with it.
 * A lapped car's gap is in laps, not seconds, so it falls back to its own
 * timing.
 */
import type { DriverRow } from '$lib/timing-tower';

/** A crossing this many laps overdue is not a late message; the car has
 * stopped (retired, red flag) and is not drawn at all. */
export const MAX_OVERDUE_LAPS = 3;

export interface LapTiming {
	/** Laps elapsed since this driver last crossed the line, at their own
	 * last-lap pace. Can exceed 1 when a crossing is late. */
	lapsSinceLine(driver: string): number | null;
	/** Their last lap, seconds. */
	lapTimeS(driver: string): number | null;
}

/** Wrap a lap count onto the circuit, or `null` when it is overdue. */
function onLap(laps: number | null): number | null {
	if (laps === null) return null;
	if (laps < 0) return 0;
	if (laps > MAX_OVERDUE_LAPS) return null;
	return laps % 1;
}

export function placeCars(rows: readonly DriverRow[], timing: LapTiming): Map<string, number> {
	const out = new Map<string, number>();
	const leader = rows.find((r) => r.position === 1);
	const leaderFraction = leader ? onLap(timing.lapsSinceLine(leader.driver)) : null;
	const leaderLapS = leader ? timing.lapTimeS(leader.driver) : null;
	for (const row of rows) {
		if (row.position === null) continue;
		if (leader && row.driver === leader.driver) {
			if (leaderFraction !== null) out.set(row.driver, leaderFraction);
			continue;
		}
		const gap = row.gapToLeaderS;
		if (
			leaderFraction !== null &&
			leaderLapS !== null &&
			leaderLapS > 0 &&
			gap !== null &&
			row.lapsDown === null
		) {
			const behind = gap / leaderLapS;
			out.set(row.driver, (((leaderFraction - behind) % 1) + 1) % 1);
			continue;
		}
		const own = onLap(timing.lapsSinceLine(row.driver));
		if (own !== null) out.set(row.driver, own);
	}
	return out;
}
