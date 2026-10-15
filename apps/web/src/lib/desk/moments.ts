/** Which released moments get a toast (ROADMAP W2 D4: "new moments arrive
 * as toasts"). Only explanations Delay Sync has already released are ever
 * read, and only ones released close to the viewer's clock get a toast:
 * joining a live race late releases its backlog all at once, and replaying
 * twenty old moments as toasts would bury the screen. A fallback ("no clear
 * cause") is never toasted; it says nothing new. */
import type { Envelope } from '$lib/delay-sync';
import { anomalyKindLabel, explanationViewFromEnvelope } from '$lib/why/view';

/** How close to the clock a moment must be to count as just happened. */
export const FRESH_MS = 15_000;

export interface MomentToast {
	id: string;
	kicker: string;
	text: string;
}

/** Folds the released log once, from where it last stopped, so each
 * explanation is considered exactly once, toasted or not. */
export class MomentToasts {
	private cursor = 0;
	private readonly seen = new Set<string>();

	constructor(private readonly freshMs = FRESH_MS) {}

	/** The toast for the newest moment released since the last call, if
	 * it is fresh. */
	take(events: readonly Envelope[], clockMs: number): MomentToast[] {
		if (events.length < this.cursor) this.reset();
		const out: MomentToast[] = [];
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope?.kind !== 'explanation') continue;
			const view = explanationViewFromEnvelope(envelope);
			if (view === null || this.seen.has(view.anomalyId)) continue;
			this.seen.add(view.anomalyId);
			const at = Date.parse(envelope.event_time_utc);
			if (!view.grounded || !Number.isFinite(at) || clockMs - at > this.freshMs) continue;
			out.push({
				id: view.anomalyId,
				kicker: [
					view.lapNumber !== null ? `Lap ${view.lapNumber}` : null,
					anomalyKindLabel(view.kind)
				]
					.filter(Boolean)
					.join(' · '),
				text: view.text
			});
		}
		this.cursor = events.length;
		// Several at once is a burst (a replay at full speed): toast only the
		// newest rather than stack them over the race.
		return out.slice(-1);
	}

	reset(): void {
		this.cursor = 0;
		this.seen.clear();
	}
}
