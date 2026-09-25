/** What the desk reads off the released event log beyond the timing
 * tower: the track status chip, and the "Radio & race control" lane.
 * Everything here folds envelopes Delay Sync has already released, so
 * nothing can surface ahead of the viewer's clock. */
import type { Envelope } from '$lib/delay-sync';
import { radioClipViewFromEnvelope, type RadioClipView } from '$lib/radio/view';

export type TrackStatusTone = 'green' | 'yellow' | 'sc' | 'vsc' | 'red' | 'chequered';

export interface TrackStatusChip {
	tone: TrackStatusTone;
	label: string;
}

const STATUS_LABEL: Record<TrackStatusTone, string> = {
	green: 'Green',
	yellow: 'Yellow',
	sc: 'Safety car',
	vsc: 'VSC',
	red: 'Red flag',
	chequered: 'Chequered'
};

/** The latest released track status; chequered once the session is over. */
export function trackStatusChip(events: readonly Envelope[], finished: boolean): TrackStatusChip {
	if (finished) return { tone: 'chequered', label: STATUS_LABEL.chequered };
	for (let i = events.length - 1; i >= 0; i--) {
		const envelope = events[i];
		if (envelope?.kind !== 'track_status_changed') continue;
		const status = envelope.payload.status;
		if (typeof status === 'string' && status in STATUS_LABEL) {
			const tone = status as TrackStatusTone;
			return { tone, label: STATUS_LABEL[tone] };
		}
	}
	return { tone: 'green', label: STATUS_LABEL.green };
}

export interface RaceControlItem {
	type: 'rc';
	id: string;
	time: string;
	message: string;
	lap: number | null;
	flag: string | null;
	category: string | null;
}

export interface RadioItem {
	type: 'radio';
	id: string;
	time: string;
	clip: RadioClipView;
}

export type FeedItem = RaceControlItem | RadioItem;

/** Blue flags are the bulk of a race's race-control traffic and tell a
 * viewer nothing about the race they are watching; everything else stays. */
function isNoise(message: string, flag: string | null): boolean {
	return flag === 'BLUE' || /WAVED BLUE FLAG/i.test(message);
}

/** Race-control messages and radio clips, newest first. */
export class RaceFeedReducer {
	private readonly items: FeedItem[] = [];
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		if (events.length < this.cursor) this.reset();
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue;
			if (envelope.kind === 'race_control_message') {
				const p = envelope.payload;
				const message = typeof p.message === 'string' ? p.message : null;
				if (message === null) continue;
				const flag = typeof p.flag === 'string' ? p.flag : null;
				if (isNoise(message, flag)) continue;
				this.items.push({
					type: 'rc',
					id: `rc-${i}`,
					time: envelope.event_time_utc,
					message,
					lap: typeof p.lap === 'number' ? p.lap : null,
					flag,
					category: typeof p.category === 'string' ? p.category : null
				});
			} else if (envelope.kind === 'radio_clip') {
				const clip = radioClipViewFromEnvelope(envelope);
				if (clip === null) continue;
				this.items.push({ type: 'radio', id: `radio-${i}`, time: envelope.event_time_utc, clip });
			}
		}
		this.cursor = events.length;
	}

	/** Newest first, optionally one type only. A fresh array every call:
	 * Svelte's `$derived` must see a new reference to re-render. */
	list(filter: 'all' | 'radio' | 'rc' = 'all'): FeedItem[] {
		const kept = filter === 'all' ? this.items : this.items.filter((item) => item.type === filter);
		return [...kept].reverse();
	}

	reset(): void {
		this.items.length = 0;
		this.cursor = 0;
	}
}
