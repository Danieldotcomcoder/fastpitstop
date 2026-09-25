/** Pure transform for the session-detail "Race positions" section: `/laps`
 * (every lap, every driver, already fuel/clean-lap annotated by
 * `f1_kernel.annotate_laps`) sliced down to one time series per selected
 * driver, plus the two pieces of race context that make a position chart
 * readable rather than decorative — where each driver pitted, and when the
 * race was neutralised.
 *
 * No REST/WS in here — the route fetches `/laps` once and this module
 * reshapes what came back, the same "load, then pass frames in" split the
 * kernel itself uses. Everything is a pure function so it is testable with
 * no DOM, matching every other `$lib` core in this app. */
import type { components } from '$lib/api/client';

type LapRow = components['schemas']['LapRowOut'];

export type RaceShapeMetric = 'position' | 'gap_to_leader';

export interface RaceShapePoint {
	lapNumber: number;
	gapToLeaderS: number | null;
	position: number | null;
	/** Tyre on this lap — drives the compound dots along a driver's line. */
	compound: string | null;
	/** The lap the car came into the pits on: where a position line's big
	 * drop is EXPLAINED rather than looking like a mystery. */
	isPitIn: boolean;
	isPitOut: boolean;
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
			position: lap.position,
			compound: lap.compound,
			isPitIn: lap.is_pit_in === true,
			isPitOut: lap.is_pit_out === true
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

/** A driver's final (highest-lap) known position — what the right-hand
 * edge labels sort by, so the label column reads as the running order at
 * the playback clock instead of an arbitrary driver-number order. */
export function latestPosition(shape: DriverRaceShape): number | null {
	for (let i = shape.points.length - 1; i >= 0; i--) {
		const position = shape.points[i]?.position;
		if (position !== null && position !== undefined) return position;
	}
	return null;
}

export interface TrackStatusBand {
	fromLap: number;
	/** Inclusive: a one-lap band has `fromLap === toLap`. */
	toLap: number;
	status: 'sc' | 'vsc' | 'red';
}

const BAND_RANK: Record<TrackStatusBand['status'], number> = { vsc: 1, sc: 2, red: 3 };

function lapStatus(lap: LapRow): TrackStatusBand['status'] | null {
	if (lap.is_red) return 'red';
	if (lap.is_sc) return 'sc';
	if (lap.is_vsc) return 'vsc';
	return null;
}

/** Laps the race was neutralised on, as merged bands to shade behind the
 * chart. Derived across the WHOLE field, not one driver: a lap is
 * neutralised for everyone, but any single driver may have been in the
 * pits (and so unflagged) for part of it. The most severe status wins on a
 * lap flagged several ways, matching `f1_kernel`'s own track-status
 * severity ordering.
 *
 * This is what turns a confusing cluster of crossing lines into "of course
 * — that was the safety car", which is exactly the context a public
 * timing screen never gives you. */
export function trackStatusBands(laps: LapRow[]): TrackStatusBand[] {
	const byLap = new Map<number, TrackStatusBand['status']>();
	for (const lap of laps) {
		const status = lapStatus(lap);
		if (status === null) continue;
		const existing = byLap.get(lap.lap_number);
		if (existing === undefined || BAND_RANK[status] > BAND_RANK[existing]) {
			byLap.set(lap.lap_number, status);
		}
	}
	const bands: TrackStatusBand[] = [];
	for (const lapNumber of [...byLap.keys()].sort((a, b) => a - b)) {
		const status = byLap.get(lapNumber) as TrackStatusBand['status'];
		const last = bands[bands.length - 1];
		if (last !== undefined && last.status === status && last.toLap === lapNumber - 1) {
			last.toLap = lapNumber;
			continue;
		}
		bands.push({ fromLap: lapNumber, toLap: lapNumber, status });
	}
	return bands;
}

export interface PitMarker {
	driver: string;
	lapNumber: number;
}

/** Every pit entry among the selected drivers, as chart markers. */
export function pitMarkers(series: DriverRaceShape[]): PitMarker[] {
	const markers: PitMarker[] = [];
	for (const shape of series) {
		for (const point of shape.points) {
			if (point.isPitIn) markers.push({ driver: shape.driver, lapNumber: point.lapNumber });
		}
	}
	return markers;
}

/** Teammates share a team colour, so the second car of each team gets a
 * dashed stroke — the convention every F1 broadcast graphic uses, and the
 * reason a 20-line chart can be team-coloured at all instead of falling
 * back to 20 arbitrary hues (10 of which repeat).
 *
 * Ordering is by the driver order handed in (the running order), so the
 * car currently ahead is the solid one. A driver with no known team is
 * always solid: an unknown team must not silently read as "second car". */
export function teammateDashOrder(
	drivers: readonly string[],
	teamOf: (driver: string) => string | null
): Record<string, boolean> {
	const seenPerTeam = new Map<string, number>();
	const dashed: Record<string, boolean> = {};
	for (const driver of drivers) {
		const team = teamOf(driver);
		if (!team) {
			dashed[driver] = false;
			continue;
		}
		const seen = seenPerTeam.get(team) ?? 0;
		dashed[driver] = seen > 0;
		seenPerTeam.set(team, seen + 1);
	}
	return dashed;
}
