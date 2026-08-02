/** Folds the visible domain-event log into the live/replay explanation
 * lane's ordered list — reads ONLY `Envelope`s
 * `VisibleSessionState.events` already contains, so this needs no REST
 * call and is spoiler-safe by construction. An `explanation` envelope's
 * own pacing is already gated on the anomaly's `anomaly_time`
 * (`f1_api.stream.replay`'s explanation flush), so by the time one
 * reaches `visibleState.events` it is provably not ahead of the user's
 * playback clock — the same reasoning `$lib/radio/lane.ts` gives for
 * skipping a separate reveal gate. */
import type { Envelope } from '$lib/delay-sync';
import { explanationViewFromEnvelope, type ExplanationView } from './view';

/** Cursor-based fold over `VisibleSessionState.events` — the same
 * "only process newly-appended events" discipline every other reducer in
 * this codebase follows. Deduplicates by `anomalyId`. */
export class ExplanationLaneReducer {
	private readonly seen = new Set<string>();
	private readonly ordered: ExplanationView[] = [];
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue;
			if (envelope.kind !== 'explanation') continue;
			const explanation = explanationViewFromEnvelope(envelope);
			if (explanation === null) continue;
			if (this.seen.has(explanation.anomalyId)) continue;
			this.seen.add(explanation.anomalyId);
			this.ordered.push(explanation);
		}
		this.cursor = events.length;
	}

	/** A fresh array every call — NOT `this.ordered` directly. Svelte 5's
	 * `$derived.by` compares by reference; handing back the same
	 * mutated-in-place array would make Svelte see "no change" even after
	 * new explanations were pushed — the exact `RadioLaneReducer.clips` /
	 * `TimingTowerReducer.rows` caveat. */
	get explanations(): readonly ExplanationView[] {
		return [...this.ordered];
	}

	reset(): void {
		this.seen.clear();
		this.ordered.length = 0;
		this.cursor = 0;
	}
}
