/** Measured car positions, smoothed for drawing.
 *
 * The feed samples every car about four times a second (`car_positions`
 * frames, released through Delay Sync into `VisibleSessionState.
 * positionFrames`). Drawn as they arrive, cars would hop 20 m at a time.
 * Instead the map draws a moment BEHIND the playback clock (`lagMs`): far
 * enough back that the sample after the drawn instant has usually been
 * released already, so each car glides between two real samples. Drawing
 * the past is always spoiler-safe; when the next sample is late, the car
 * holds at its newest one rather than being extrapolated off the track.
 *
 * How far behind is FIXED for a given replay speed (`lagMs`). It used to
 * follow the measured spacing of recent frames, and that median wobbles by
 * a few milliseconds from one release to the next: at 16x every wobble
 * moved the drawn instant by a car length, ten times a second, which is
 * what made the cars judder. Two samples further apart than a couple of
 * seconds of race time are a discontinuity (the car was in the garage, or
 * the feed lost it), not a path: the car holds rather than gliding across
 * the circuit between them.
 *
 * Samples are SMOOTHED in time, not joined point to point. F1 stamps
 * positions loosely: consecutive samples imply a car swinging between 35
 * and 75 m/s within a second (2024 Bahrain, Verstappen, mid-race), because
 * a sample's timestamp is its batch's, not the instant it was measured.
 * Joining them made every car surge and stall a few times a second. At
 * each drawn instant `at` fits a line through the car's samples within
 * about half a second either side (Gaussian weights, `SMOOTHING_MS` of race
 * time), which averages the timestamp noise out and moves continuously.
 *
 * Pure: reads only envelopes Delay Sync already released, and the clock it
 * is handed. */
import { type Envelope, eventTimeMs } from '$lib/delay-sync';

export interface MeasuredPosition {
	x: number;
	y: number;
	/** How long before the drawn instant the car's newest sample is, in ms:
	 * 0 while it is between two samples, growing while it holds at the
	 * newest one. */
	holdMs: number;
	/** How far the car's newest sample trails the newest frame for ANY car,
	 * in ms. Large means the feed has stopped placing this car while it
	 * still places others (garage, retirement); when the whole feed stops
	 * (the session ended, a stall) every car keeps 0 and stays drawn. */
	behindFeedMs: number;
}

interface Sample {
	tMs: number;
	x: number;
	y: number;
}

interface ParsedFrame {
	tMs: number;
	cars: [string, number, number][];
}

/** Parsed once per envelope: the same frame stays in the window for many
 * redraws. */
const parsedFrames = new WeakMap<Envelope, ParsedFrame | null>();

function parseFrame(envelope: Envelope): ParsedFrame | null {
	const cached = parsedFrames.get(envelope);
	if (cached !== undefined) return cached;
	let parsed: ParsedFrame | null = null;
	const cars = envelope.payload.cars;
	if (typeof cars === 'object' && cars !== null && !Array.isArray(cars)) {
		const out: [string, number, number][] = [];
		for (const [driver, point] of Object.entries(cars as Record<string, unknown>)) {
			if (!Array.isArray(point)) continue;
			const [x, y] = point as unknown[];
			if (typeof x === 'number' && typeof y === 'number' && Number.isFinite(x + y)) {
				out.push([driver, x, y]);
			}
		}
		parsed = { tMs: eventTimeMs(envelope), cars: out };
	}
	parsedFrames.set(envelope, parsed);
	return parsed;
}

/** F1 samples each car about every 0.25 s of race time; a little more so
 * the next sample has usually arrived by the time it is needed. */
export const FEED_SAMPLE_INTERVAL_MS = 280;
/** Two samples further apart than this (race time) are not interpolated. */
export const MAX_INTERPOLATION_GAP_MS = 2000;
/** The smoothing kernel's width (one standard deviation, race time). */
export const SMOOTHING_MS = 250;
/** Allowance for the socket delivering a frame after its own timestamp. */
const ARRIVAL_SLACK_MS = 200;

export class MeasuredPositions {
	private tracks = new Map<string, Sample[]>();
	private window: readonly Envelope[] | null = null;
	private newestMs = -Infinity;

	/** Re-read the released window. A no-op when it has not changed, which
	 * is most redraws: Delay Sync releases ten times a second, the map draws
	 * sixty. */
	update(frames: readonly Envelope[]): void {
		if (frames === this.window) return;
		this.window = frames;
		const tracks = new Map<string, Sample[]>();
		const times: number[] = [];
		for (const envelope of frames) {
			const frame = parseFrame(envelope);
			if (frame === null) continue;
			times.push(frame.tMs);
			for (const [driver, x, y] of frame.cars) {
				let track = tracks.get(driver);
				if (track === undefined) {
					track = [];
					tracks.set(driver, track);
				}
				track.push({ tMs: frame.tMs, x, y });
			}
		}
		// Released in time order; a frame that arrived late can still land
		// after a newer one.
		for (const track of tracks.values()) {
			for (let i = 1; i < track.length; i++) {
				if (track[i]!.tMs < track[i - 1]!.tMs) {
					track.sort((a, b) => a.tMs - b.tMs);
					break;
				}
			}
		}
		this.tracks = tracks;
		this.newestMs = times.length > 0 ? Math.max(...times) : -Infinity;
	}

	get hasPositions(): boolean {
		return this.tracks.size > 0;
	}

	/** How far behind the playback clock to draw, in stream milliseconds:
	 * one and a half feed intervals (race time, so it shrinks as the replay
	 * speeds up), plus Delay Sync's release tick, plus arrival slack. A
	 * function of the speed alone, never of what just arrived; see the
	 * module docstring. */
	static lagMs(releaseTickMs: number, timeScale: number): number {
		const scale = Math.max(1, timeScale);
		return (FEED_SAMPLE_INTERVAL_MS * 1.5) / scale + releaseTickMs + ARRIVAL_SLACK_MS;
	}

	/** Where `driver` was at `renderMs`. With `smoothMs` > 0 (the map's
	 * setting), a weighted line through the samples within ~3 `smoothMs`
	 * either side, never reaching across a gap wider than `maxGapMs`; with
	 * 0, straight interpolation between the two samples either side. Held at
	 * the nearest end when there is no sample on one side, and at the
	 * earlier sample across a gap. `null` when the window never placed this
	 * car. */
	at(driver: string, renderMs: number, maxGapMs = Infinity, smoothMs = 0): MeasuredPosition | null {
		const track = this.tracks.get(driver);
		if (track === undefined || track.length === 0) return null;
		const last = track[track.length - 1]!;
		const behindFeedMs = Math.max(0, this.newestMs - last.tMs);
		if (smoothMs > 0) return smoothed(track, renderMs, maxGapMs, smoothMs, behindFeedMs);
		if (renderMs >= last.tMs) {
			return { x: last.x, y: last.y, holdMs: renderMs - last.tMs, behindFeedMs };
		}
		const first = track[0]!;
		if (renderMs <= first.tMs) return { x: first.x, y: first.y, holdMs: 0, behindFeedMs };
		// The first sample strictly after renderMs.
		let lo = 0;
		let hi = track.length - 1;
		while (lo < hi) {
			const mid = (lo + hi) >> 1;
			if (track[mid]!.tMs <= renderMs) lo = mid + 1;
			else hi = mid;
		}
		const after = track[lo]!;
		const before = track[lo - 1]!;
		const span = after.tMs - before.tMs;
		if (span > maxGapMs) {
			return { x: before.x, y: before.y, holdMs: renderMs - before.tMs, behindFeedMs };
		}
		const t = span > 0 ? (renderMs - before.tMs) / span : 1;
		return {
			x: before.x + (after.x - before.x) * t,
			y: before.y + (after.y - before.y) * t,
			holdMs: 0,
			behindFeedMs
		};
	}

	reset(): void {
		this.tracks = new Map();
		this.window = null;
		this.newestMs = -Infinity;
	}
}

/** First index whose sample is strictly after `t`. */
function firstAfter(track: readonly Sample[], t: number): number {
	let lo = 0;
	let hi = track.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (track[mid]!.tMs <= t) lo = mid + 1;
		else hi = mid;
	}
	return lo;
}

/** Locally weighted linear regression of x and y on time, evaluated at
 * `renderMs` (clamped to the samples' own span, never extrapolated past
 * the newest). The window stops at `maxGapMs` gaps and at three kernel
 * widths. */
function smoothed(
	track: readonly Sample[],
	renderMs: number,
	maxGapMs: number,
	smoothMs: number,
	behindFeedMs: number
): MeasuredPosition {
	const first = track[0]!;
	const last = track[track.length - 1]!;
	const t = Math.min(Math.max(renderMs, first.tMs), last.tMs);
	const holdMs = Math.max(0, renderMs - last.tMs);
	const after = firstAfter(track, t);
	// Across a gap the car is where it was last seen, not on a line.
	if (after > 0 && after < track.length) {
		const before = track[after - 1]!;
		if (track[after]!.tMs - before.tMs > maxGapMs) {
			return { x: before.x, y: before.y, holdMs: renderMs - before.tMs, behindFeedMs };
		}
	}
	const reach = smoothMs * 3;
	let lo = Math.max(0, after - 1);
	while (
		lo > 0 &&
		track[lo - 1]!.tMs >= t - reach &&
		track[lo]!.tMs - track[lo - 1]!.tMs <= maxGapMs
	) {
		lo--;
	}
	let hi = Math.min(track.length - 1, after);
	while (
		hi < track.length - 1 &&
		track[hi + 1]!.tMs <= t + reach &&
		track[hi + 1]!.tMs - track[hi]!.tMs <= maxGapMs
	) {
		hi++;
	}
	let s0 = 0;
	let s1 = 0;
	let s2 = 0;
	let sx = 0;
	let sy = 0;
	let sxd = 0;
	let syd = 0;
	for (let i = lo; i <= hi; i++) {
		const sample = track[i]!;
		const d = (sample.tMs - t) / smoothMs;
		const w = Math.exp(-0.5 * d * d);
		s0 += w;
		s1 += w * d;
		s2 += w * d * d;
		sx += w * sample.x;
		sy += w * sample.y;
		sxd += w * sample.x * d;
		syd += w * sample.y * d;
	}
	const det = s0 * s2 - s1 * s1;
	if (s0 <= 0) return { x: last.x, y: last.y, holdMs, behindFeedMs };
	if (det <= 1e-9 * s0 * s0) {
		return { x: sx / s0, y: sy / s0, holdMs, behindFeedMs };
	}
	return {
		x: (s2 * sx - s1 * sxd) / det,
		y: (s2 * sy - s1 * syd) / det,
		holdMs,
		behindFeedMs
	};
}
