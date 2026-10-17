/** The seam a view actually talks to: wires the playback clock, the
 * `TimelineBuffer` gate, and the derived `visibleState` store together, plus
 * the ways a viewer lines the page up with their TV (ROADMAP W6): the
 * slider, a tap at lights out, a tap as the leader crosses the line,
 * starting from lights out on a recording (catch-up), and pause and resume.
 * A view subscribes to `visibleState`/`delayState` only — there is no method
 * here that hands back anything from the buffer directly.
 *
 * **The clock** (`clockMs`) is the one thing every gate reads: wall time
 * minus the delay, or, while paused, frozen where the pause began. Resuming
 * adds exactly the time spent paused to the delay, so the clock carries on
 * from where it stopped and never jumps: nothing the viewer's TV has not
 * shown can be released across a pause. */
import { delaySecondsFromTap } from './calibration';
import { clamp } from './clock';
import type { Envelope } from './envelope';
import { crossingOf, nearestCrossing, type PickedCrossing } from './resync';
import { Store } from './store';
import { TimelineBuffer } from './timeline-buffer';
import {
	applyReleasedEnvelopes,
	EMPTY_VISIBLE_STATE,
	type VisibleSessionState
} from './visible-state';

/** How the delay was last set. */
export type SyncMethod = 'default' | 'slider' | 'lights-out' | 'leader' | 'catch-up';

export interface DelayState {
	delaySeconds: number;
	/** True once the user has explicitly set a delay (slider or tap) rather
	 * than running at the untouched default. The "synced" half of the
	 * always-visible "current delay + synced state" indicator. */
	synced: boolean;
	/** Frozen with the viewer's TV: the clock stands still until resumed. */
	paused: boolean;
	method: SyncMethod;
	/** When the viewer last set the delay (slider, tap, catch-up), on the
	 * clock the controller was given; null until they do. Streams drift, so
	 * a sync's age is part of how far to trust it (`syncQuality`). */
	setAtMs: number | null;
	/** The lap whose leader crossing a "leader crossed the line" tap matched. */
	anchorLap: number | null;
}

/** What a replacement controller needs to carry on exactly where this one
 * stands (a reconnect, a new replay speed): the delay, how it was set, and
 * a pause in progress. */
export interface DelayCarry {
	delaySeconds: number;
	method: SyncMethod;
	setAtMs: number | null;
	anchorLap: number | null;
	pausedAtMs: number | null;
}

export interface DelaySyncOptions {
	initialDelaySeconds?: number;
	minDelaySeconds?: number;
	/** The slider's top. A pause, catch-up or a tap can go beyond it, up to
	 * `maxTotalDelaySeconds`. */
	maxDelaySeconds?: number;
	/** How far behind live the page can ever run: a whole session watched
	 * from its start, plus pauses. */
	maxTotalDelaySeconds?: number;
	/** Carry another controller's state over; overrides `initialDelaySeconds`. */
	carry?: DelayCarry;
	/** Wall-clock reader, epoch ms. Defaults to `Date.now` — injectable so
	 * tests never depend on real time. */
	now?: () => number;
}

const DEFAULT_MIN_DELAY_SECONDS = 0;
const DEFAULT_MAX_DELAY_SECONDS = 120;
/** A day: no session (with every red flag) and no pause gets near it, so
 * the clamp never cuts a delay short, which would release what the viewer
 * has not seen. */
const DEFAULT_MAX_TOTAL_DELAY_SECONDS = 24 * 3600;

export class DelaySyncController {
	readonly visibleState = new Store<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	readonly delayState: Store<DelayState>;
	readonly minDelaySeconds: number;
	readonly maxDelaySeconds: number;
	readonly maxTotalDelaySeconds: number;

	private readonly buffer = new TimelineBuffer();
	private readonly now: () => number;
	private pausedAtMs: number | null = null;
	/** Lap -> the first crossing completing it (the leader's), epoch ms. */
	private readonly crossings = new Map<number, number>();

	constructor(options: DelaySyncOptions = {}) {
		this.now = options.now ?? Date.now;
		this.minDelaySeconds = options.minDelaySeconds ?? DEFAULT_MIN_DELAY_SECONDS;
		this.maxDelaySeconds = options.maxDelaySeconds ?? DEFAULT_MAX_DELAY_SECONDS;
		this.maxTotalDelaySeconds = Math.max(
			this.maxDelaySeconds,
			options.maxTotalDelaySeconds ?? DEFAULT_MAX_TOTAL_DELAY_SECONDS
		);
		const carry = options.carry;
		if (carry) {
			this.pausedAtMs = carry.pausedAtMs;
			this.delayState = new Store<DelayState>({
				delaySeconds: this.total(carry.delaySeconds),
				synced: carry.method !== 'default',
				paused: carry.pausedAtMs !== null,
				method: carry.method,
				setAtMs: carry.setAtMs,
				anchorLap: carry.anchorLap
			});
			return;
		}
		const initial = clamp(
			options.initialDelaySeconds ?? this.minDelaySeconds,
			this.minDelaySeconds,
			this.maxDelaySeconds
		);
		this.delayState = new Store<DelayState>({
			delaySeconds: initial,
			synced: false,
			paused: false,
			method: 'default',
			setAtMs: null,
			anchorLap: null
		});
	}

	private total(delaySeconds: number): number {
		return clamp(delaySeconds, this.minDelaySeconds, this.maxTotalDelaySeconds);
	}

	private set(delaySeconds: number, method: SyncMethod, atMs: number, anchorLap: number | null) {
		this.delayState.update((state) => ({
			...state,
			delaySeconds,
			synced: true,
			method,
			setAtMs: atMs,
			anchorLap
		}));
	}

	/** The playback clock, epoch ms: everything at or before it may show. */
	clockMs(nowMs: number = this.now()): number {
		const delayMs = this.delayState.get().delaySeconds * 1000;
		return (this.pausedAtMs ?? nowMs) - delayMs;
	}

	/** Queue one envelope as it arrives off the transport (WebSocket). This
	 * never touches `visibleState` — release only ever happens in `tick`. */
	ingest(envelope: Envelope): void {
		this.buffer.ingest(envelope);
		const crossing = crossingOf(envelope);
		if (crossing !== null) {
			const known = this.crossings.get(crossing.lap);
			if (known === undefined || crossing.eventMs < known) {
				this.crossings.set(crossing.lap, crossing.eventMs);
			}
		}
	}

	/** Manual calibration: the delay slider, within its own range. */
	setDelaySeconds(delaySeconds: number, nowMs: number = this.now()): void {
		this.set(
			clamp(delaySeconds, this.minDelaySeconds, this.maxDelaySeconds),
			'slider',
			nowMs,
			null
		);
	}

	/** Auto calibration: "tap when the lights go out" (or on any incident
	 * whose `event_time_utc` is known). `tapWallMs` is when the user tapped
	 * (epoch ms); `eventTimeUtc` is the corresponding session event's own
	 * timestamp. Returns the delay actually applied (post-clamp). */
	calibrateFromTap(tapWallMs: number, eventTimeUtc: string | number): number {
		const seconds = clamp(
			delaySecondsFromTap(tapWallMs, eventTimeUtc),
			this.minDelaySeconds,
			this.maxDelaySeconds
		);
		this.set(seconds, 'lights-out', tapWallMs, null);
		return seconds;
	}

	/** Catch-up: the viewer starts a recording (or a late stream) from its
	 * lights out, and taps as the lights go out there. The same arithmetic as
	 * `calibrateFromTap` with no slider cap: an hour behind live is the
	 * point. A page that has already shown more than that must be rebuilt
	 * from the start (`carry` into a fresh controller), since nothing
	 * released can be taken back. Returns the delay applied. */
	catchUpFrom(tapWallMs: number, lightsOutUtc: string | number): number {
		const seconds = this.total(delaySecondsFromTap(tapWallMs, lightsOutUtc));
		this.set(seconds, 'catch-up', tapWallMs, null);
		return seconds;
	}

	/** "Tap as the leader crosses the line", at any moment of a race: lines
	 * the clock up with the leader's crossing nearest to it (the first car
	 * to complete each lap, from the stream's own timestamps, released or
	 * not — timestamps only, never content). Null when there is none
	 * within reach. */
	resyncOnLeader(tapWallMs: number): PickedCrossing | null {
		const crossings = [...this.crossings].map(([lap, eventMs]) => ({ lap, eventMs }));
		const picked = nearestCrossing(crossings, tapWallMs, this.delayState.get().delaySeconds, {
			min: this.minDelaySeconds,
			max: this.maxTotalDelaySeconds
		});
		if (picked === null) return null;
		this.set(picked.delaySeconds, 'leader', tapWallMs, picked.lap);
		return picked;
	}

	/** Freeze the clock with the viewer's TV. */
	pause(nowMs: number = this.now()): void {
		if (this.pausedAtMs !== null) return;
		this.pausedAtMs = nowMs;
		this.delayState.update((state) => ({ ...state, paused: true }));
	}

	/** Carry on from where the pause froze the clock: the delay grows by
	 * exactly the time spent paused. */
	resume(nowMs: number = this.now()): void {
		if (this.pausedAtMs === null) return;
		const pausedFor = Math.max(0, nowMs - this.pausedAtMs) / 1000;
		this.pausedAtMs = null;
		this.delayState.update((state) => ({
			...state,
			paused: false,
			delaySeconds: this.total(state.delaySeconds + pausedFor)
		}));
	}

	/** Everything a replacement controller needs to carry on from here. */
	carry(): DelayCarry {
		const state = this.delayState.get();
		return {
			delaySeconds: state.delaySeconds,
			method: state.method,
			setAtMs: state.setAtMs,
			anchorLap: state.anchorLap,
			pausedAtMs: this.pausedAtMs
		};
	}

	/** Release everything now due and fold it into `visibleState`. Call this
	 * on an interval/rAF. The only place envelopes cross from the buffer
	 * into anything a view can see. */
	tick(nowMs: number = this.now()): Envelope[] {
		const released = this.buffer.release(this.clockMs(nowMs));
		if (released.length > 0) {
			this.visibleState.update((state) => applyReleasedEnvelopes(state, released));
		}
		return released;
	}

	get pendingCount(): number {
		return this.buffer.pendingCount;
	}

	/** The newest event time received so far, released or not (epoch ms):
	 * how far ahead of the clock the feed has delivered. Null before any. */
	get newestReceivedMs(): number | null {
		return this.buffer.newestEventMs;
	}

	/** Whether any leader crossing is known yet (for enabling the tap). */
	get hasLeaderCrossings(): boolean {
		return this.crossings.size > 0;
	}
}
