import type { Envelope } from '$lib/delay-sync';

export interface RaceLap {
	/** The lap the race is ON, from the feed's own `LapCount` topic. */
	current: number;
	/** Scheduled race distance, when the feed has said. */
	total: number | null;
	/** True when `current` came from the feed rather than being inferred
	 * from completed laps. */
	fromFeed: boolean;
}

/** The race's current lap, preferring the feed's own counter.
 *
 * `max(lap_completed)` is the last lap a driver FINISHED, so it trails the
 * television by at least a lap and by more whenever ingest is behind — the
 * 2026 Spanish GP showed it several laps adrift. `lap_counter_updated`
 * carries the number the timing screen shows, so it wins whenever we have
 * it; the completed-lap figure stays as the honest fallback for sessions
 * recorded before that event existed.
 */
export function raceLap(events: readonly Envelope[], completedLaps: number): RaceLap {
	let current: number | null = null;
	let total: number | null = null;
	for (const envelope of events) {
		if (envelope.kind !== 'lap_counter_updated') continue;
		const lap = envelope.payload.current_lap;
		const laps = envelope.payload.total_laps;
		if (typeof lap === 'number') current = lap;
		if (typeof laps === 'number') total = laps;
	}
	if (current === null) {
		return { current: Math.max(0, completedLaps), total, fromFeed: false };
	}
	// Never go backwards: a late-arriving completed lap must not drag the
	// counter below what the feed already announced.
	return { current: Math.max(current, completedLaps), total, fromFeed: true };
}
