/** The "visible session state" the whole point of gating exists to feed:
 * the ONLY thing any view is allowed to subscribe to. It is built
 * exclusively by folding envelopes `TimelineBuffer.release` already handed
 * back — there is no constructor path that takes raw/pending envelopes, so
 * a view reading this can never see anything ahead of the playback clock.
 *
 * This deliberately stays generic (a running log of released domain-event
 * envelopes, plus a pointer at the latest released kernel snapshot) rather
 * than modeling full race state (current lap, positions, gaps per driver) —
 * that per-domain-event reducer is the actual race views' job (track map,
 * degradation/undercut panels), still not built per docs/PHASE3.md, not
 * this module's. Delay Sync's job stops at "here is everything due so far,
 * in order, spoiler-safe" — a later reducer can fold `events` into whatever
 * shape a specific view needs without ever touching the buffer directly. */
import { type Envelope, type KernelSnapshotPayload, isKernelSnapshot } from './envelope';

export interface VisibleSessionState {
	/** Released envelopes whose kind is a domain event (i.e. not
	 * `kernel_snapshot`), in event-time order. */
	events: Envelope[];
	/** The most recently released `kernel_snapshot` payload, if any has been
	 * released yet. */
	latestSnapshot: KernelSnapshotPayload | null;
	/** `event_time_utc` of the most recently released envelope of any kind —
	 * the playback progress marker a view can show ("caught up to 14:32:07"). */
	lastEventTimeUtc: string | null;
}

export const EMPTY_VISIBLE_STATE: VisibleSessionState = Object.freeze({
	events: [],
	latestSnapshot: null,
	lastEventTimeUtc: null
});

/** Pure reducer: fold one already-released envelope into visible state.
 * Never inspects wall time or a delay — by the time an envelope reaches
 * here the gate has already decided it is due. */
export function applyReleasedEnvelope(
	state: VisibleSessionState,
	envelope: Envelope
): VisibleSessionState {
	if (isKernelSnapshot(envelope)) {
		return {
			...state,
			latestSnapshot: envelope.payload as unknown as KernelSnapshotPayload,
			lastEventTimeUtc: envelope.event_time_utc
		};
	}
	return {
		...state,
		events: [...state.events, envelope],
		lastEventTimeUtc: envelope.event_time_utc
	};
}

/** Folds a whole batch in event-time order — what a controller calls once
 * per `tick` with everything `TimelineBuffer.release` just returned.
 *
 * Deliberately NOT `envelopes.reduce(applyReleasedEnvelope, state)`: that
 * spreads `state.events` into a fresh array on every single envelope in the
 * batch, which is one array copy per envelope rather than one per tick. A
 * `speed=20` replay can release thousands of envelopes in one 250ms tick
 * against an `events` array already tens of thousands long — reduced over
 * `applyReleasedEnvelope` that is O(released × total) per tick, which is what
 * pegged the browser tab and ballooned memory the first time this ran
 * against a real (not the tiny checked-in fixture) race. This walks the
 * batch once, appends every domain-event envelope to ONE new array via a
 * single spread, and folds `kernel_snapshot`s into `latestSnapshot` along
 * the way — same per-envelope semantics as `applyReleasedEnvelope`, applied
 * in the same order, at O(released + total) per tick instead. */
export function applyReleasedEnvelopes(
	state: VisibleSessionState,
	envelopes: readonly Envelope[]
): VisibleSessionState {
	if (envelopes.length === 0) return state;

	let latestSnapshot = state.latestSnapshot;
	let lastEventTimeUtc = state.lastEventTimeUtc;
	const newDomainEvents: Envelope[] = [];

	for (const envelope of envelopes) {
		lastEventTimeUtc = envelope.event_time_utc;
		if (isKernelSnapshot(envelope)) {
			latestSnapshot = envelope.payload as unknown as KernelSnapshotPayload;
		} else {
			newDomainEvents.push(envelope);
		}
	}

	return {
		events: newDomainEvents.length > 0 ? [...state.events, ...newDomainEvents] : state.events,
		latestSnapshot,
		lastEventTimeUtc
	};
}
