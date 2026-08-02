/** Pure transform for the session-detail "Race shape" section: `/laps`
 * (every lap, every driver, already fuel/clean-lap annotated by
 * `f1_kernel.annotate_laps`) sliced down to one time series per selected
 * driver. No REST/WS in here — the route fetches `/laps` once and this
 * module reshapes what came back, the same "load, then pass frames in"
 * split the kernel itself uses. */
import type { components } from '$lib/api/client';

type LapRow = components['schemas']['LapRowOut'];

export type RaceShapeMetric = 'gap_to_leader' | 'position';

export interface RaceShapePoint {
	lapNumber: number;
	gapToLeaderS: number | null;
	position: number | null;
}

export interface DriverRaceShape {
	driver: string;
	points: RaceShapePoint[];
}

export function buildRaceShapeSeries(laps: LapRow[], drivers: string[]): DriverRaceShape[] {
	const wanted = new Set(drivers);
	const byDriver = new Map<string, RaceShapePoint[]>();
	for (const lap of laps) {
		if (!wanted.has(lap.driver)) continue;
		const points = byDriver.get(lap.driver) ?? [];
		points.push({
			lapNumber: lap.lap_number,
			gapToLeaderS: lap.gap_to_leader_s,
			position: lap.position
		});
		byDriver.set(lap.driver, points);
	}
	return drivers
		.filter((driver) => byDriver.has(driver))
		.map((driver) => ({
			driver,
			points: (byDriver.get(driver) ?? []).slice().sort((a, b) => a.lapNumber - b.lapNumber)
		}));
}

export function maxLapNumber(series: DriverRaceShape[]): number {
	let max = 0;
	for (const s of series) for (const p of s.points) max = Math.max(max, p.lapNumber);
	return max;
}

/** Gap chart's y-axis: 0 (the leader) at the top, worst gap at the bottom —
 * matches how a running order actually reads. Returns `null` when no
 * selected driver has any gap data yet (still loading, or a qualifying/
 * practice session where gap-to-leader isn't meaningful). */
export function maxGapToLeader(series: DriverRaceShape[]): number | null {
	let max: number | null = null;
	for (const s of series) {
		for (const p of s.points) {
			if (p.gapToLeaderS === null) continue;
			max = max === null ? p.gapToLeaderS : Math.max(max, p.gapToLeaderS);
		}
	}
	return max;
}

export function maxPosition(series: DriverRaceShape[]): number | null {
	let max: number | null = null;
	for (const s of series) {
		for (const p of s.points) {
			if (p.position === null) continue;
			max = max === null ? p.position : Math.max(max, p.position);
		}
	}
	return max;
}
