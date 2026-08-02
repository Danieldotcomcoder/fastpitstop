/** The seam a view actually talks to: folds `VisibleSessionState.events`
 * (Delay Sync's spoiler-safe log — see `$lib/delay-sync`) into car marker
 * state and segment dominance in one pass. Keeps a CURSOR into the log
 * rather than re-scanning it from the start on every call — `events` only
 * ever grows over a session, and re-folding the whole thing on every
 * animation-frame tick would be quadratic over a full race replay. */
import { type Envelope, eventTimeMs } from '$lib/delay-sync';
import { applyEnvelope, createRaceState, type RaceState } from './car-state';
import { buildArcLengthTable } from './arc-length';
import { DominanceTracker } from './dominance';
import type { TrackOutline } from './types';

export class RaceStateReducer {
	readonly raceState: RaceState = createRaceState();
	private cursor = 0;
	private dominanceTracker: DominanceTracker | null = null;

	/** (Re)build the outline-dependent dominance tracker. `null` when no
	 * circuit/outline is available for this session — dominance painting is
	 * then simply skipped, the "degrade gracefully on sparse/missing
	 * position data" case the outline itself can't do anything about. */
	setOutline(outline: TrackOutline | null): void {
		this.dominanceTracker = outline ? new DominanceTracker(buildArcLengthTable(outline.points)) : null;
	}

	get dominance(): DominanceTracker | null {
		return this.dominanceTracker;
	}

	/** Process every envelope appended to `events` since the last call. */
	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue; // unreachable: i is always < events.length here
			const tsMs = eventTimeMs(envelope);
			applyEnvelope(this.raceState, envelope, tsMs);
			if (envelope.kind === 'car_position' && this.dominanceTracker) {
				this._ingestDominance(envelope, tsMs);
			}
		}
		this.cursor = events.length;
	}

	private _ingestDominance(envelope: Envelope, tsMs: number): void {
		const { driver, x, y } = envelope.payload;
		if (typeof driver !== 'string' || typeof x !== 'number' || typeof y !== 'number') return;
		const compound = this.raceState.cars.get(driver)?.compound ?? null;
		this.dominanceTracker?.ingest(driver, x, y, tsMs, compound);
	}

	/** Drop all accumulated state — a session switch, not a mid-session
	 * event. `setOutline` is unaffected: callers re-set it explicitly once
	 * the new session's circuit has loaded. */
	reset(): void {
		this.raceState.cars.clear();
		this.cursor = 0;
		this.dominanceTracker?.reset();
	}
}
