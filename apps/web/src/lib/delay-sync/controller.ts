/** The seam a view actually talks to: wires the playback clock, the
 * `TimelineBuffer` gate, and the derived `visibleState` store together, plus
 * the two calibration paths (manual slider, auto tap-to-sync). A view
 * subscribes to `visibleState`/`delayState` only — there is no method here
 * that hands back anything from the buffer directly. */
import { delaySecondsFromTap } from './calibration';
import { clamp, playbackClockMs } from './clock';
import type { Envelope } from './envelope';
import { Store } from './store';
import { TimelineBuffer } from './timeline-buffer';
import {
	applyReleasedEnvelopes,
	EMPTY_VISIBLE_STATE,
	type VisibleSessionState
} from './visible-state';

export interface DelayState {
	delaySeconds: number;
	/** True once the user has explicitly set a delay (slider or tap) rather
	 * than running at the untouched default. The "synced" half of the
	 * always-visible "current delay + synced state" indicator. */
	synced: boolean;
}

export interface DelaySyncOptions {
	initialDelaySeconds?: number;
	minDelaySeconds?: number;
	maxDelaySeconds?: number;
	/** Wall-clock reader, epoch ms. Defaults to `Date.now` — injectable so
	 * tests never depend on real time. */
	now?: () => number;
}

const DEFAULT_MIN_DELAY_SECONDS = 0;
const DEFAULT_MAX_DELAY_SECONDS = 120;

export class DelaySyncController {
	readonly visibleState = new Store<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	readonly delayState: Store<DelayState>;
	readonly minDelaySeconds: number;
	readonly maxDelaySeconds: number;

	private readonly buffer = new TimelineBuffer();
	private readonly now: () => number;

	constructor(options: DelaySyncOptions = {}) {
		this.now = options.now ?? Date.now;
		this.minDelaySeconds = options.minDelaySeconds ?? DEFAULT_MIN_DELAY_SECONDS;
		this.maxDelaySeconds = options.maxDelaySeconds ?? DEFAULT_MAX_DELAY_SECONDS;
		const initial = clamp(
			options.initialDelaySeconds ?? this.minDelaySeconds,
			this.minDelaySeconds,
			this.maxDelaySeconds
		);
		this.delayState = new Store<DelayState>({ delaySeconds: initial, synced: false });
	}

	/** Queue one envelope as it arrives off the transport (WebSocket). This
	 * never touches `visibleState` — release only ever happens in `tick`. */
	ingest(envelope: Envelope): void {
		this.buffer.ingest(envelope);
	}

	/** Manual calibration: the delay slider. */
	setDelaySeconds(delaySeconds: number): void {
		const clamped = clamp(delaySeconds, this.minDelaySeconds, this.maxDelaySeconds);
		this.delayState.set({ delaySeconds: clamped, synced: true });
	}

	/** Auto calibration: "tap when the lights go out" (or on any incident
	 * whose `event_time_utc` is known). `tapWallMs` is when the user tapped
	 * (epoch ms); `eventTimeUtc` is the corresponding session event's own
	 * timestamp. Returns the delay actually applied (post-clamp). */
	calibrateFromTap(tapWallMs: number, eventTimeUtc: string | number): number {
		this.setDelaySeconds(delaySecondsFromTap(tapWallMs, eventTimeUtc));
		return this.delayState.get().delaySeconds;
	}

	/** Release everything now due and fold it into `visibleState`. Call this
	 * on an interval/rAF. The only place envelopes cross from the buffer
	 * into anything a view can see. */
	tick(nowMs: number = this.now()): Envelope[] {
		const clockMs = playbackClockMs(nowMs, this.delayState.get().delaySeconds);
		const released = this.buffer.release(clockMs);
		if (released.length > 0) {
			this.visibleState.update((state) => applyReleasedEnvelopes(state, released));
		}
		return released;
	}

	get pendingCount(): number {
		return this.buffer.pendingCount;
	}
}
