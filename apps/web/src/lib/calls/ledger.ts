/** Folds released `call` envelopes into the race's calls and scorecard
 * (ROADMAP W5). Reads ONLY `VisibleSessionState.events`, which Delay Sync
 * has already released, and each envelope is stamped no earlier than the
 * crossing that locked or settled its call (`f1_api.calls`). So a call, and
 * every right or wrong on the scorecard, appears only once the viewer's
 * clock has reached it: no separate reveal gate is needed. */
import type { Envelope } from '$lib/delay-sync';
import { callFromEnvelope, type CallConfidence, type CallOut } from './view';

export interface ScoreLine {
	confidence: CallConfidence;
	right: number;
	wrong: number;
}

export interface Scorecard {
	right: number;
	wrong: number;
	/** High, medium, low. */
	byConfidence: readonly ScoreLine[];
	pending: number;
	void: number;
	noCall: number;
}

const LEVELS: readonly CallConfidence[] = ['high', 'medium', 'low'];

export function scorecardOf(calls: readonly CallOut[]): Scorecard {
	const byConfidence = LEVELS.map((confidence) => ({
		confidence,
		right: calls.filter((c) => c.confidence === confidence && c.status === 'right').length,
		wrong: calls.filter((c) => c.confidence === confidence && c.status === 'wrong').length
	}));
	return {
		right: byConfidence.reduce((n, line) => n + line.right, 0),
		wrong: byConfidence.reduce((n, line) => n + line.wrong, 0),
		byConfidence,
		pending: calls.filter((c) => c.status === 'pending').length,
		void: calls.filter((c) => c.status === 'void').length,
		noCall: calls.filter((c) => c.status === 'no_call').length
	};
}

/** Cursor-based, like every reducer over the released log. A call's later
 * envelope (its status changed) replaces the earlier one in place, so the
 * list keeps the order calls first locked in. */
export class CallLedgerReducer {
	private readonly byId = new Map<string, CallOut>();
	private cursor = 0;
	/** Bumps whenever a call is added or changes. */
	version = 0;

	update(events: readonly Envelope[]): void {
		if (events.length < this.cursor) this.reset();
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue;
			const call = callFromEnvelope(envelope);
			if (call === null) continue;
			this.byId.set(call.call_id, call);
			this.version++;
		}
		this.cursor = events.length;
	}

	/** A fresh array every call (Svelte 5's `$derived` compares by reference). */
	get calls(): readonly CallOut[] {
		return [...this.byId.values()];
	}

	get scorecard(): Scorecard {
		return scorecardOf([...this.byId.values()]);
	}

	reset(): void {
		this.byId.clear();
		this.cursor = 0;
		this.version++;
	}
}
