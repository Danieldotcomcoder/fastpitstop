/** Shared shapes for the timing tower — the per-domain-event reducer
 * `$lib/delay-sync/visible-state.ts` explicitly left to "the actual race
 * views' job" (the same seam `$lib/track-map/car-state.ts` and
 * `$lib/kernel-views/gap-stream.ts` already fill for their own views). One
 * row per driver, folded from `position_changed` / `gap_updated` /
 * `lap_completed` / `stint_started` / `pit_entry` / `pit_exit` /
 * `driver_list_updated` envelopes only — every field stays `null` until the
 * feed has actually said something about it, never a fabricated zero. */

export interface DriverRow {
	driver: string;
	code: string | null;
	name: string | null;
	team: string | null;
	/** Classification position from `position_changed`. `null` until the feed
	 * has placed this driver at least once. */
	position: number | null;
	/** The FIRST position this driver was ever seen in — the grid slot, as
	 * far as the released event log can know. `null` until they are placed
	 * once. Never revised afterwards, which is the point: it is the baseline
	 * `positionsGained` is measured from. */
	startPosition: number | null;
	/** Seconds behind the leader, or `null` when unknown OR when lapped (see
	 * `lapsDown`) — mutually exclusive, mirroring `f1_domain.GapUpdated`. */
	gapToLeaderS: number | null;
	lapsDown: number | null;
	intervalToAheadS: number | null;
	lapNumber: number | null;
	lastLapTimeS: number | null;
	isPersonalBest: boolean | null;
	compound: string | null;
	tyreAge: number | null;
	stintNumber: number | null;
	/** True from `pit_entry` until the matching `pit_exit`. */
	inPit: boolean;
}

export function createDriverRow(driver: string): DriverRow {
	return {
		driver,
		code: null,
		name: null,
		team: null,
		position: null,
		startPosition: null,
		gapToLeaderS: null,
		lapsDown: null,
		intervalToAheadS: null,
		lapNumber: null,
		lastLapTimeS: null,
		isPersonalBest: null,
		compound: null,
		tyreAge: null,
		stintNumber: null,
		inPit: false
	};
}

/** Places gained (positive) or lost (negative) since this driver's first
 * seen position, or `null` when either end is unknown. Zero is a real
 * answer — "held station" — and stays distinguishable from unknown. */
export function positionsGained(row: DriverRow): number | null {
	if (row.position === null || row.startPosition === null) return null;
	return row.startPosition - row.position;
}
