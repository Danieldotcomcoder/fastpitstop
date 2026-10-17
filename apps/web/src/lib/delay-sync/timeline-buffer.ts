/** The whole spoiler-safety guarantee lives in one method: `release`. Every
 * envelope arriving off the WebSocket is `ingest`ed and held; `release`
 * hands back — and only hands back — the ones whose `event_time_utc` is at
 * or before the playback clock passed in. Nothing else in this file, this
 * module, or the app is allowed a path from "just arrived" to "on screen"
 * that skips this check.
 *
 * Kept ordered by event time (not arrival order) specifically so an
 * out-of-order arrival — a slightly-early WebSocket message, a reconnect —
 * still releases in the right sequence and never early. */
import { type Envelope, eventTimeMs } from './envelope';

export class TimelineBuffer {
	private readonly pending: Envelope[] = [];
	private readonly releasedLog: Envelope[] = [];
	private newest: number | null = null;

	/** Insert into `pending`, kept sorted ascending by `event_time_utc`. */
	ingest(envelope: Envelope): void {
		const ms = eventTimeMs(envelope);
		if (this.newest === null || ms > this.newest) this.newest = ms;
		let low = 0;
		let high = this.pending.length;
		while (low < high) {
			const mid = (low + high) >>> 1;
			const candidate = this.pending[mid];
			if (candidate !== undefined && eventTimeMs(candidate) <= ms) {
				low = mid + 1;
			} else {
				high = mid;
			}
		}
		this.pending.splice(low, 0, envelope);
	}

	/** Moves every pending envelope with `event_time_utc <= playbackClockMs`
	 * out of `pending` and into the released history, in ascending
	 * event-time order, and returns exactly the ones released THIS call.
	 * Idempotent: an envelope is returned by exactly one `release` call ever
	 * — calling again with the same (or an earlier) clock returns `[]`. Pure
	 * with respect to `playbackClockMs`: the same buffer state and the same
	 * clock always release the same envelopes. */
	release(playbackClockMs: number): Envelope[] {
		const releasedNow: Envelope[] = [];
		while (this.pending.length > 0) {
			const next = this.pending[0];
			if (next === undefined || eventTimeMs(next) > playbackClockMs) break;
			this.pending.shift();
			releasedNow.push(next);
			this.releasedLog.push(next);
		}
		return releasedNow;
	}

	/** The newest event time ever ingested (epoch ms), released or not. */
	get newestEventMs(): number | null {
		return this.newest;
	}

	get pendingCount(): number {
		return this.pending.length;
	}

	get releasedCount(): number {
		return this.releasedLog.length;
	}

	/** Every envelope ever released, in release (= event-time) order. A
	 * snapshot copy — mutating it does not affect the buffer. */
	get released(): Envelope[] {
		return [...this.releasedLog];
	}

	/** Every envelope still held back, in event-time order. A snapshot copy. */
	get pendingEnvelopes(): Envelope[] {
		return [...this.pending];
	}
}
