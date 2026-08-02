/** The spoiler gate for kernel analytics that don't arrive over the
 * WebSocket at all (`DegradationFitOut`, `BrakingDriftOut` — the
 * `kernel_snapshot` envelope only ever carries crossovers, per
 * `f1_api.stream.replay.kernel_snapshot_envelope`). Those two REST
 * responses describe the WHOLE session up front, so rendering them
 * directly the moment they arrive would show stints that haven't happened
 * yet on the user's delayed playback clock — the same leak Delay Sync
 * exists to prevent for the raw event stream.
 *
 * The fix follows the exact pattern `TimelineBuffer` already uses: fetch
 * (ingest) everything, but only ever REVEAL what's provably already in the
 * past. Here "provably in the past" is derived purely from
 * `VisibleSessionState.events` — domain events Delay Sync has ALREADY
 * released — never from a wall clock or a second network call, so this
 * stays exactly as spoiler-safe as the events it reads:
 *
 * - a STINT is over, and its `DegradationFitOut`/`BrakingDriftOut` safe to
 *   show, once the driver has been seen starting a LATER stint (proves
 *   every lap of this one already happened), or the session itself has
 *   finished (covers the still-open final stint);
 * - a single LAP's own facts (one `BrakingPointRowOut` row) are safe to
 *   show once that lap number has actually been completed (a `lap_completed`
 *   event for it has been released) — a finer-grained, more precise
 *   version of the same idea for data keyed by lap rather than by stint.
 *
 * This never recomputes an analytic (CLAUDE.md's Phase 3 invariant) — it
 * only decides WHEN an already-fitted kernel value may be displayed. */
import type { Envelope } from '$lib/delay-sync';

const TERMINAL_SESSION_STATUSES = new Set(['finished', 'finalised', 'ends']);

export interface RevealState {
	/** driver -> highest `stint_number` seen in a `stint_started` event. */
	latestStintNumber: Map<string, number>;
	/** driver -> highest `lap_number` seen in a `lap_completed` event. */
	maxCompletedLap: Map<string, number>;
	/** A terminal `session_status_changed` or a `CHEQUERED` race-control
	 * message has been observed — only source that can ever reveal a
	 * driver's still-open FINAL stint. */
	sessionFinished: boolean;
}

export function createRevealState(): RevealState {
	return { latestStintNumber: new Map(), maxCompletedLap: new Map(), sessionFinished: false };
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function applyRevealEnvelope(state: RevealState, envelope: Envelope): void {
	const payload = envelope.payload;
	switch (envelope.kind) {
		case 'stint_started': {
			const driver = asString(payload.driver);
			const stintNumber = asNumber(payload.stint_number);
			if (driver === null || stintNumber === null) return;
			const current = state.latestStintNumber.get(driver) ?? 0;
			if (stintNumber > current) state.latestStintNumber.set(driver, stintNumber);
			return;
		}
		case 'lap_completed': {
			const driver = asString(payload.driver);
			const lapNumber = asNumber(payload.lap_number);
			if (driver === null || lapNumber === null) return;
			const current = state.maxCompletedLap.get(driver) ?? 0;
			if (lapNumber > current) state.maxCompletedLap.set(driver, lapNumber);
			return;
		}
		case 'session_status_changed': {
			const status = asString(payload.status);
			if (status !== null && TERMINAL_SESSION_STATUSES.has(status.toLowerCase())) {
				state.sessionFinished = true;
			}
			return;
		}
		case 'race_control_message': {
			const flag = asString(payload.flag);
			if (flag !== null && flag.toUpperCase() === 'CHEQUERED') state.sessionFinished = true;
			return;
		}
		default:
			return;
	}
}

/** Cursor-based fold over `VisibleSessionState.events` — the same "only
 * process what's newly appended" discipline `RaceStateReducer`
 * (`$lib/track-map`) uses, since `events` only ever grows over a session. */
export class RevealTracker {
	readonly state: RevealState = createRevealState();
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue; // unreachable: i is always < events.length here
			applyRevealEnvelope(this.state, envelope);
		}
		this.cursor = events.length;
	}

	/** Safe to show a (driver, stint) fit? Fails closed: an unseen driver or
	 * stint is never revealed. */
	isStintRevealed(driver: string, stintNumber: number): boolean {
		const latest = this.state.latestStintNumber.get(driver);
		if (latest === undefined) return false;
		if (latest > stintNumber) return true;
		return latest === stintNumber && this.state.sessionFinished;
	}

	/** Safe to show one lap's own row (e.g. a braking-point observation)? */
	isLapRevealed(driver: string, lapNumber: number): boolean {
		const maxLap = this.state.maxCompletedLap.get(driver);
		return maxLap !== undefined && lapNumber <= maxLap;
	}

	reset(): void {
		this.state.latestStintNumber.clear();
		this.state.maxCompletedLap.clear();
		this.state.sessionFinished = false;
		this.cursor = 0;
	}
}
