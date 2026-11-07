/** The timing screen's mini-sectors, placed on the track.
 *
 * The live feed says when each car completes each of the lap's mini-sectors
 * (about two dozen, each ending at a timing point) and how quick it was,
 * never where on the track that is. The circuit's pace profile says where a
 * car is at each fraction of a clean lap's time; so a mini-sector that cars
 * complete, on average, 31% of the way through their laps ends where the
 * profile puts a car at 31%. On the 2026 Dutch GP that landed a median 7 m
 * from MultiViewer's own drawing of the boundaries.
 *
 * The same timing pins every car down about two dozen times a lap: a car
 * that completed mini-sector k at `t` was at its end then, and moves on at
 * its own pace (over its last few mini-sectors), shaped by the profile,
 * until it completes the next. Each car from its own timing, never the
 * leader's: the gap estimate (placement.ts) moved the whole field whenever
 * the leader crossed the line after an unusual lap or the lead changed,
 * which is the jump seen at the line in the 2026 US GP replay.
 *
 * A session with no mini-sectors (stored before 2026-10-30, or backfilled)
 * still has the three sector times a lap: those are a coarse layout of the
 * same kind (`layout === 'sectors'`), used until a mini-sector arrives.
 *
 * Folds only released envelopes (Delay Sync's), cursor-based like every
 * other reducer here.
 */
import type { Envelope } from '$lib/delay-sync';

export type MiniSectorStatus = 'normal' | 'personal_best' | 'overall_best' | 'pit_lane' | 'other';

export interface Completion {
	/** 0-based across the lap. */
	index: number;
	lap: number | null;
	status: MiniSectorStatus;
	/** When it happened, race time: the event's own `ts_utc`. */
	atMs: number;
	/** When its envelope was stamped on the stream (`event_time_utc`): what
	 * the viewer's clock is compared with. A replay stamps `speed` times
	 * faster than the race ran. */
	stampMs: number;
}

/** Laps used to place the boundaries: within this of the session's quick
 * laps (in-laps, out-laps, safety cars and the first lap fall out). */
const CLEAN_LAP_RATIO = 1.07;
/** Which of the session's lap times counts as a quick lap: a low
 * percentile, since in practice and qualifying half the laps are out-laps
 * and in-laps. */
const QUICK_LAP_PERCENTILE = 0.25;
/** A car's pace is read over its newest stretches, at least `MIN_PACE_SPAN`
 * of a lap (up to `PACE_MINI_SECTORS` of them), so it follows a safety car
 * or a push lap at once. */
const PACE_MINI_SECTORS = 4;
/** Race time after which a car's next timing point is taken whatever it is
 * (`movesOn`). */
const RESYNC_MS = 30_000;
const MIN_PACE_SPAN = 0.08;
/** Samples kept per mini-sector, newest first. */
const MAX_SAMPLES = 120;
/** Lap times the session must have before any is taken as the quick-lap
 * reference: more than one round of the field. The first laps after a
 * start or a red-flag restart all include the wait (the 2026 Dutch GP's
 * took 140 s against 76 s racing laps) and would otherwise pass as clean. */
const MIN_LAP_TIMES = 30;
/** Before this many samples of every mini-sector, none is placed. */
const MIN_SAMPLES = 3;
/** A car whose next timing point is this late is held just short of it
 * rather than run past it (and pulled back when it arrives). */
const HOLD_SHORT = 0.97;
/** Past this much of its expected time to the next timing point overdue, a
 * car's anchor has expired (stopped, in the pits): it is not placed from it. */
const MAX_OVERDUE = 3;

interface DriverTiming {
	/** Line crossings, envelope time, by lap number completed. */
	laps: Map<number, number>;
	/** Completions waiting for their lap to finish, by lap number. */
	pending: Map<number, Completion[]>;
	latest: Completion | null;
	/** The last few completions, oldest first: the car's current pace. */
	recent: Completion[];
	/** The latest lap's completions, by index. */
	lapStatuses: Map<number, MiniSectorStatus>;
	lapOfStatuses: number | null;
	lastLapMs: number | null;
	/** When the lap `lastLapMs` timed began. */
	lastLapStartMs: number | null;
	/** When the car last left the pits: no stretch before it sets its pace. */
	pitExitMs: number | null;
}

const STATUSES: ReadonlySet<string> = new Set([
	'normal',
	'personal_best',
	'overall_best',
	'pit_lane',
	'other'
]);

function blank(): DriverTiming {
	return {
		laps: new Map(),
		pending: new Map(),
		latest: null,
		recent: [],
		lapStatuses: new Map(),
		lapOfStatuses: null,
		lastLapMs: null,
		lastLapStartMs: null,
		pitExitMs: null
	};
}

/** `profile` at fraction-of-lap-time `tau`, linearly between its samples. */
export function profileAt(profile: readonly number[], tau: number): number {
	const n = profile.length - 1;
	if (n < 1) return tau;
	const x = Math.min(1, Math.max(0, tau)) * n;
	const i = Math.min(n - 1, Math.floor(x));
	const a = profile[i] ?? 0;
	const b = profile[i + 1] ?? 1;
	return a + (b - a) * (x - i);
}

function median(values: readonly number[]): number {
	const sorted = [...values].sort((a, b) => a - b);
	const mid = sorted.length >> 1;
	return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

function percentile(values: readonly number[], p: number): number {
	const sorted = [...values].sort((a, b) => a - b);
	return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))]!;
}

export class MiniSectorTracker {
	private readonly drivers = new Map<string, DriverTiming>();
	/** Fraction of lap time at which each mini-sector ends, per sample. */
	private readonly taus = new Map<number, { tau: number; lapMs: number }[]>();
	private readonly lapTimes: number[] = [];
	private cursor = 0;
	private count: number | null = null;
	private kind: 'mini_sectors' | 'sectors' | null = null;
	private boundaryCache: { profile: readonly number[]; fractions: number[] } | null = null;
	/** `boundaryTaus`' answer, until a new sample moves it. */
	private tauCache: number[] | null = null;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope) this.apply(envelope);
		}
		this.cursor = events.length;
	}

	reset(): void {
		this.drivers.clear();
		this.taus.clear();
		this.lapTimes.length = 0;
		this.cursor = 0;
		this.count = null;
		this.kind = null;
		this.boundaryCache = null;
		this.tauCache = null;
	}

	/** A new lap layout: nothing placed on the old one applies, but the
	 * line crossings and lap times stand. */
	private relayout(kind: 'mini_sectors' | 'sectors', count: number): void {
		this.taus.clear();
		this.boundaryCache = null;
		this.tauCache = null;
		for (const timing of this.drivers.values()) {
			timing.pending.clear();
			timing.latest = null;
			timing.recent = [];
			timing.lapStatuses = new Map();
			timing.lapOfStatuses = null;
		}
		this.kind = kind;
		this.count = count;
	}

	/** How many timing points this session's lap has, once any is seen. */
	get miniSectorCount(): number | null {
		return this.count;
	}

	/** What the timing points are: the timing screen's mini-sectors, or
	 * (a session without them) its three sectors. */
	get layout(): 'mini_sectors' | 'sectors' | null {
		return this.kind;
	}

	private entry(driver: string): DriverTiming {
		let found = this.drivers.get(driver);
		if (!found) {
			found = blank();
			this.drivers.set(driver, found);
		}
		return found;
	}

	private apply(envelope: Envelope): void {
		const payload = envelope.payload;
		const driver = payload.driver;
		if (typeof driver !== 'string') return;
		// Every interval is measured in race time, so lap times and pace hold
		// whatever the replay speed, and across a change of speed; only
		// `fractionAt`'s extrapolation from the stream clock uses the speed.
		const stampMs = Date.parse(envelope.event_time_utc);
		if (Number.isNaN(stampMs)) return;
		const raceMs = typeof payload.ts_utc === 'string' ? Date.parse(payload.ts_utc) : NaN;
		const atMs = Number.isNaN(raceMs) ? stampMs : raceMs;
		if (envelope.kind === 'lap_completed') {
			const lap = payload.lap_number;
			if (typeof lap === 'number') this.completeLap(driver, lap, atMs);
			return;
		}
		if (envelope.kind === 'pit_exit') {
			this.entry(driver).pitExitMs = atMs;
			return;
		}
		let index: unknown;
		let count: unknown;
		let status: MiniSectorStatus = 'other';
		if (envelope.kind === 'mini_sector_completed') {
			index = payload.mini_sector;
			count = payload.mini_sector_count;
			if (STATUSES.has(String(payload.status))) status = payload.status as MiniSectorStatus;
		} else if (envelope.kind === 'sector_completed' && this.kind !== 'mini_sectors') {
			index = typeof payload.sector === 'number' ? payload.sector - 1 : null;
			count = 3;
		} else {
			return;
		}
		if (typeof index !== 'number' || typeof count !== 'number' || index < 0 || index >= count) {
			return;
		}
		const kind = envelope.kind === 'mini_sector_completed' ? 'mini_sectors' : 'sectors';
		if (this.kind !== kind || this.count !== count) this.relayout(kind, count);
		const lap = typeof payload.lap_number === 'number' ? payload.lap_number : null;
		const completion: Completion = { index, lap, status, atMs, stampMs };
		const timing = this.entry(driver);
		if (this.movesOn(timing.latest, completion)) {
			timing.latest = completion;
			timing.recent.push(completion);
			if (timing.recent.length > PACE_MINI_SECTORS + 1) timing.recent.shift();
		}
		if (lap !== null && lap !== timing.lapOfStatuses) {
			if (timing.lapOfStatuses === null || lap > timing.lapOfStatuses) {
				timing.lapOfStatuses = lap;
				timing.lapStatuses = new Map();
			}
		}
		if (lap === timing.lapOfStatuses) timing.lapStatuses.set(index, status);
		if (lap !== null) {
			const started = timing.laps.get(lap - 1);
			const ended = timing.laps.get(lap);
			if (started !== undefined && ended !== undefined) {
				this.sample(completion, started, ended);
			} else if (ended === undefined) {
				const waiting = timing.pending.get(lap) ?? [];
				waiting.push(completion);
				timing.pending.set(lap, waiting);
			}
		}
	}

	/** Whether `next` is where the car is now: up to half a lap on from
	 * `latest`. The feed delivers some timing points late, after the next
	 * lap's first (2026 Dutch GP), and one of those must not drag the car
	 * back round the lap. A car silent for `RESYNC_MS` takes whatever comes
	 * next. */
	private movesOn(latest: Completion | null, next: Completion): boolean {
		if (!latest || this.count === null) return true;
		if (next.atMs < latest.atMs) return false;
		// The same timing point again: the feed re-sends a car's last sector
		// after a reconnect (the 2026 Dutch GP's, 14:00), however long the
		// car has been silent.
		if (next.index === latest.index && next.lap === latest.lap) return false;
		if (next.atMs - latest.atMs > RESYNC_MS) return true;
		const ahead = (next.index - latest.index + this.count) % this.count;
		// With three sectors a lap, a missed one leaves the next two ahead.
		const reach = this.count <= 3 ? this.count - 1 : this.count / 2;
		return ahead > 0 && ahead <= reach;
	}

	private completeLap(driver: string, lap: number, atMs: number): void {
		const timing = this.entry(driver);
		timing.laps.set(lap, atMs);
		const started = timing.laps.get(lap - 1);
		if (started !== undefined && atMs > started) {
			timing.lastLapMs = atMs - started;
			timing.lastLapStartMs = started;
			this.lapTimes.push(atMs - started);
			// The quick-lap reference moved: which samples are clean may too.
			this.tauCache = null;
			this.boundaryCache = null;
			for (const completion of timing.pending.get(lap) ?? []) {
				this.sample(completion, started, atMs);
			}
		}
		timing.pending.delete(lap);
	}

	private quickLapMs(): number | null {
		return this.lapTimes.length >= MIN_LAP_TIMES
			? percentile(this.lapTimes.slice(-200), QUICK_LAP_PERCENTILE)
			: null;
	}

	private sample(completion: Completion, startedMs: number, endedMs: number): void {
		const duration = endedMs - startedMs;
		const quick = this.quickLapMs();
		if (quick === null || duration > quick * CLEAN_LAP_RATIO) return;
		if (completion.status === 'pit_lane') return;
		const tau = (completion.atMs - startedMs) / duration;
		if (tau < 0 || tau > 1.05) return;
		const samples = this.taus.get(completion.index) ?? [];
		samples.push({ tau: Math.min(1, tau), lapMs: duration });
		if (samples.length > MAX_SAMPLES) samples.shift();
		this.taus.set(completion.index, samples);
		this.boundaryCache = null;
		this.tauCache = null;
	}

	/** Where each mini-sector ends, as lap fractions in index order, or
	 * `null` until every one has been seen on enough clean laps. Never
	 * decreasing; the last is the line. */
	boundaries(profile: readonly number[] | null): number[] | null {
		if (!profile || profile.length < 2) return null;
		if (this.boundaryCache?.profile === profile) return this.boundaryCache.fractions;
		const taus = this.boundaryTaus();
		if (!taus) return null;
		const fractions = taus.map((tau, k) => (k === taus.length - 1 ? 1 : profileAt(profile, tau)));
		this.boundaryCache = { profile, fractions };
		return fractions;
	}

	/** The fraction of lap TIME at which each mini-sector ends, matching
	 * `boundaries`: the median over the samples whose lap was clean by the
	 * session's quick lap as it stands now, so a sample taken before the
	 * reference settled drops out once it has. */
	private boundaryTaus(): number[] | null {
		if (this.tauCache) return this.tauCache;
		const quick = this.quickLapMs();
		if (this.count === null || quick === null) return null;
		const out: number[] = [];
		for (let k = 0; k < this.count; k++) {
			const clean = (this.taus.get(k) ?? [])
				.filter((sample) => sample.lapMs <= quick * CLEAN_LAP_RATIO)
				.map((sample) => sample.tau);
			if (clean.length < MIN_SAMPLES) return null;
			out.push(k === this.count - 1 ? 1 : Math.max(median(clean), out[k - 1] ?? 0));
		}
		this.tauCache = out;
		return out;
	}

	/** How much of a lap's time lies from the end of mini-sector `from` to the
	 * end of `to`, going forward (across the line if need be). */
	private static span(taus: readonly number[], from: number, to: number): number {
		return (taus[to] ?? 1) - (taus[from] ?? 0) + (to <= from ? 1 : 0);
	}

	/** The car's lap time at its current pace: over its last few
	 * mini-sectors, else its last lap, else the session's quick lap. */
	private paceLapMs(timing: DriverTiming, taus: readonly number[]): number | null {
		const recent = timing.recent;
		const quick = this.quickLapMs();
		// The newest stretches covering at least `MIN_PACE_SPAN`: a few
		// mini-sectors, or a single sector, so the pace follows a restart or a
		// safety car within one timing point.
		// A stretch that spans a pit stop is no guide to the pace after it:
		// with three sectors a lap, a car fresh out of the pits crawled through
		// the next one and jumped when its time came (2026 US GP, every stop).
		const sinceMs = timing.pitExitMs ?? -Infinity;
		let span = 0;
		let oldest = recent.length - 1;
		while (oldest > 0 && span < MIN_PACE_SPAN && recent[oldest - 1]!.atMs >= sinceMs) {
			span += MiniSectorTracker.span(taus, recent[oldest - 1]!.index, recent[oldest]!.index);
			oldest -= 1;
		}
		if (oldest < recent.length - 1) {
			const elapsed = recent.at(-1)!.atMs - recent[oldest]!.atMs;
			if (span >= MIN_PACE_SPAN && elapsed > 0) {
				const lapMs = elapsed / span;
				// A burst of messages can make a stretch look impossibly quick.
				if (quick === null || lapMs >= quick * 0.85) return lapMs;
			}
		}
		// Nor is a lap with the stop in it.
		const stoppedLastLap =
			timing.pitExitMs !== null && timing.pitExitMs >= (timing.lastLapStartMs ?? Infinity);
		return (stoppedLastLap ? null : timing.lastLapMs) ?? quick;
	}

	/** The latest lap's completed mini-sectors for `driver`, by index. */
	lapStatuses(driver: string): ReadonlyMap<number, MiniSectorStatus> {
		return this.drivers.get(driver)?.lapStatuses ?? new Map();
	}

	/** Where `driver` is at `atMs` (the viewer's clock, on the stream's
	 * stamps) as a lap fraction, from their latest timing point; `null` when
	 * that cannot be said (no profile, boundaries not placed yet, no recent
	 * timing point).
	 *
	 * `timeScale` is race seconds per stream second, as for
	 * `LapProgressTracker`: a replay stamps envelopes that much faster, so
	 * the time since the timing point is scaled up to race time. */
	fractionAt(
		driver: string,
		atMs: number,
		profile: readonly number[] | null,
		timeScale = 1
	): number | null {
		const timing = this.drivers.get(driver);
		const latest = timing?.latest;
		if (!timing || !latest || !profile || this.count === null) return null;
		const taus = this.boundaryTaus();
		if (!taus || this.boundaries(profile) === null) return null;
		const lapMs = this.paceLapMs(timing, taus);
		if (lapMs === null || lapMs <= 0) return null;
		// The lap's last mini-sector ends at the line: the next starts from 0.
		const from = latest.index === this.count - 1 ? 0 : (taus[latest.index] ?? 0);
		const to = taus[(latest.index + 1) % this.count] ?? 1;
		const span = Math.max(1e-6, to - from);
		const elapsed = ((atMs - latest.stampMs) * (timeScale > 0 ? timeScale : 1)) / lapMs;
		const progress = elapsed / span;
		if (progress > MAX_OVERDUE) return null;
		const tau = from + span * Math.min(Math.max(progress, 0), HOLD_SHORT);
		const fraction = profileAt(profile, tau);
		return fraction >= 1 ? fraction - 1 : fraction;
	}
}
