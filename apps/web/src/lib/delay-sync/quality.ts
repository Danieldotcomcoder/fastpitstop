/** How far to trust that the page matches the picture (ROADMAP W6, "sync
 * quality indicator"). Pure: what it reads is the delay as set, how and
 * when it was set, and how far ahead of the clock the feed has delivered.
 *
 * - `paused`: frozen with the TV.
 * - `unsynced`: never lined up; the page runs at the default delay.
 * - `good`: lined up by a tap (lights out, the leader crossing the line, or
 *   the start of a recording) within `SYNC_FRESH_MS`.
 * - `check`: set by hand on the slider, or a tap long enough ago that the
 *   stream may have drifted (a TV stream rebuffers; a phone stream lags).
 * - `behind`: live only. Nothing has arrived for the last `FEED_BEHIND_MS`
 *   of the viewer's clock, so their TV may be ahead of the data. A red flag
 *   goes quiet too, which the wording allows for. */
import type { DelayState } from './controller';

export type SyncLevel = 'paused' | 'unsynced' | 'good' | 'check' | 'behind';

export interface SyncQuality {
	level: SyncLevel;
	/** A few words, for the strip. */
	label: string;
	/** One sentence: why, and what to do. */
	detail: string;
}

export interface SyncQualityInput {
	delay: DelayState;
	nowMs: number;
	/** The playback clock now, epoch ms. */
	clockMs: number;
	/** The newest event time the feed has delivered (epoch ms), or null. */
	newestReceivedMs: number | null;
	isLive: boolean;
}

/** A tap is trusted this long; after it, a re-sync is suggested. */
export const SYNC_FRESH_MS = 20 * 60_000;
/** Live data this far behind the clock reads as the feed running late. */
export const FEED_BEHIND_MS = 20_000;

const HOW: Record<DelayState['method'], string> = {
	default: '',
	slider: 'set by hand',
	'lights-out': 'from your tap at lights out',
	leader: 'from the leader crossing the line',
	'catch-up': 'from the start of your recording'
};

function minutes(ms: number): string {
	const m = Math.max(0, Math.round(ms / 60_000));
	return m < 1 ? 'just now' : m === 1 ? '1 min ago' : `${m} min ago`;
}

export function syncQuality(input: SyncQualityInput): SyncQuality {
	const { delay, nowMs, clockMs, newestReceivedMs, isLive } = input;
	if (delay.paused) {
		return {
			level: 'paused',
			label: 'Paused',
			detail: 'The page is paused with your TV. Resume when your picture does.'
		};
	}
	if (isLive && newestReceivedMs !== null && clockMs - newestReceivedMs > FEED_BEHIND_MS) {
		const late = Math.round((clockMs - newestReceivedMs) / 1000);
		return {
			level: 'behind',
			label: 'Feed behind?',
			detail: `Nothing has arrived for the last ${late} s of your clock, so your TV may be ahead of the data. A red flag goes quiet too.`
		};
	}
	if (delay.method === 'default' || delay.setAtMs === null) {
		return {
			level: 'unsynced',
			label: 'Not synced',
			detail:
				'Tap at lights out, or when the leader crosses the line, to line the page up with your picture.'
		};
	}
	const age = nowMs - delay.setAtMs;
	if (delay.method === 'slider') {
		return {
			level: 'check',
			label: 'Set by hand',
			detail: `Delay ${HOW.slider} ${minutes(age)}. A tap when the leader crosses the line is more exact.`
		};
	}
	const how =
		delay.method === 'leader' && delay.anchorLap !== null
			? `from the leader finishing lap ${delay.anchorLap}`
			: HOW[delay.method];
	if (age > SYNC_FRESH_MS) {
		return {
			level: 'check',
			label: 'Check the sync',
			detail: `Synced ${how} ${minutes(age)}. Streams drift: tap again when the leader crosses the line.`
		};
	}
	return { level: 'good', label: 'In sync', detail: `Synced ${how} ${minutes(age)}.` };
}
