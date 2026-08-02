/** Folds the visible domain-event log into the live/replay radio lane's
 * ordered clip list — reads ONLY `Envelope`s `VisibleSessionState.events`
 * already contains, so this needs no REST call and is spoiler-safe by
 * construction, the same "nothing here Delay Sync hasn't already released"
 * reasoning `$lib/kernel-views/gap-stream.ts` gives for skipping
 * `RevealTracker`: a `radio_clip` envelope's own pacing is already gated on
 * `captured_at` (`f1_api.stream.replay`'s `_event_pacing_ts`), so by the
 * time one reaches `visibleState.events` it is provably not ahead of the
 * user's playback clock. */
import type { Envelope } from '$lib/delay-sync';
import { radioClipViewFromEnvelope, type RadioClipView } from './view';

function clipKey(clip: RadioClipView): string {
	return `${clip.driver ?? ''}|${clip.capturedAt ?? ''}`;
}

/** Cursor-based fold over `VisibleSessionState.events` — the same
 * "only process newly-appended events" discipline every other reducer in
 * this codebase (`GapStreamReducer`, `TimingTowerReducer`, `RevealTracker`)
 * follows, since `events` only ever grows over a session. Deduplicates by
 * `(driver, capturedAt)` — belt-and-braces against a reconnect ever
 * re-delivering the same clip, even though `TimelineBuffer` itself never
 * releases an envelope twice. */
export class RadioLaneReducer {
	private readonly seen = new Set<string>();
	private readonly ordered: RadioClipView[] = [];
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue; // unreachable: i is always < events.length here
			if (envelope.kind !== 'radio_clip') continue;
			const clip = radioClipViewFromEnvelope(envelope);
			if (clip === null) continue;
			const key = clipKey(clip);
			if (this.seen.has(key)) continue;
			this.seen.add(key);
			this.ordered.push(clip);
		}
		this.cursor = events.length;
	}

	/** A fresh array every call — NOT `this.ordered` directly. Svelte 5's
	 * `$derived.by` compares the derived value by reference; handing back
	 * the same mutated-in-place array across calls would make Svelte see
	 * "no change" even after new clips were pushed into it, the exact
	 * `TimingTowerReducer.rows`/`TimingTower.svelte` caveat that component's
	 * own comment documents (mutating a reducer's internal state is fine —
	 * mutating what callers read back from it is not). Cheap at this
	 * clip count (a race's whole radio corpus, not a per-lap stream). */
	get clips(): readonly RadioClipView[] {
		return [...this.ordered];
	}

	reset(): void {
		this.seen.clear();
		this.ordered.length = 0;
		this.cursor = 0;
	}
}
