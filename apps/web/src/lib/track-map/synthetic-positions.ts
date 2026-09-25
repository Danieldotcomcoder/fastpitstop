/** Where each car is, when the feed never told us.
 *
 * The unauthenticated live-timing feed delivers no `Position.z` at all — the
 * 2026 Spanish GP recording contains zero position records out of 58,175 —
 * and historical/backfill sessions never carried positions in the first
 * place (DATA_CONTRACTS.md). So for almost every session there is nothing
 * measured to plot, and the map was simply absent.
 *
 * This derives a plausible position instead, from timing data we DO have:
 * a driver who completed their last lap `t` seconds ago, and whose last lap
 * took `T` seconds, is about `t/T` of the way around. Projected onto the
 * circuit outline that gives a point per car.
 *
 * It is an ESTIMATE and must be labelled as one wherever it is drawn. It
 * assumes a constant pace around the lap, so it is wrong in the ways you
 * would expect: it cannot show where on the circuit an overtake happened,
 * it drifts for a car whose current lap differs from its last, and a car in
 * the pit lane is parked at its entry point rather than driven through it.
 * What it gets right is running order, relative spacing, and the shape of
 * the field — which is what the map is for.
 */
import type { Envelope } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from '$lib/kernel-views/iso-duration';
import { buildArcLengthTable, pointAtArcLength, type ArcLengthTable } from './arc-length';
import type { PositionSample, TrackOutline } from './types';

/** Laps slower than this are treated as unusable for pacing (a safety-car
 * lap, an in-lap, a red-flag crawl). We keep the previous good lap instead
 * of stretching a car around the circuit at the wrong rate. */
const MAX_PLAUSIBLE_LAP_S = 240;
const MIN_PLAUSIBLE_LAP_S = 40;

interface DriverProgress {
	/** When this driver last crossed the line, in envelope (playback) time. */
	lastLapEndMs: number | null;
	/** Their last completed lap time, milliseconds. */
	lapTimeMs: number | null;
	lapNumber: number | null;
	inPit: boolean;
}

function blank(): DriverProgress {
	return { lastLapEndMs: null, lapTimeMs: null, lapNumber: null, inPit: false };
}

/** Folds released envelopes into per-driver lap progress.
 *
 * Cursor-based like every other reducer here (`TimingTowerReducer`,
 * `GapStreamReducer`): `events` only ever grows, so re-folding the whole log
 * on each call would be quadratic over a race.
 */
export class LapProgressTracker {
	private readonly drivers = new Map<string, DriverProgress>();
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope) this.apply(envelope);
		}
		this.cursor = events.length;
	}

	reset(): void {
		this.drivers.clear();
		this.cursor = 0;
	}

	get known(): string[] {
		return [...this.drivers.keys()];
	}

	private entry(driver: string): DriverProgress {
		let found = this.drivers.get(driver);
		if (!found) {
			found = blank();
			this.drivers.set(driver, found);
		}
		return found;
	}

	private apply(envelope: Envelope): void {
		const driver = envelope.payload.driver;
		if (typeof driver !== 'string') return;
		const atMs = Date.parse(envelope.event_time_utc);
		if (Number.isNaN(atMs)) return;

		if (envelope.kind === 'lap_completed') {
			const entry = this.entry(driver);
			const seconds = parseIsoDurationSeconds(envelope.payload.lap_time);
			entry.lastLapEndMs = atMs;
			if (seconds !== null && seconds >= MIN_PLAUSIBLE_LAP_S && seconds <= MAX_PLAUSIBLE_LAP_S) {
				entry.lapTimeMs = seconds * 1000;
			}
			const lap = envelope.payload.lap_number;
			if (typeof lap === 'number') entry.lapNumber = lap;
			return;
		}
		if (envelope.kind === 'pit_entry') {
			this.entry(driver).inPit = true;
			return;
		}
		if (envelope.kind === 'pit_exit') {
			this.entry(driver).inPit = false;
		}
	}

	/** Laps elapsed since this driver last crossed the line, unclamped (it
	 * exceeds 1 when a crossing is late). `null` before their first lap. */
	lapsSinceLine(driver: string, atMs: number, timeScale = 1): number | null {
		const entry = this.drivers.get(driver);
		if (!entry || entry.lastLapEndMs === null || entry.lapTimeMs === null) return null;
		return ((atMs - entry.lastLapEndMs) * (timeScale > 0 ? timeScale : 1)) / entry.lapTimeMs;
	}

	/** This driver's last plausible lap time, seconds. */
	lapTimeS(driver: string): number | null {
		const ms = this.drivers.get(driver)?.lapTimeMs ?? null;
		return ms === null ? null : ms / 1000;
	}

	/** How far round the lap this driver is at `atMs`, in [0, 1) — or `null`
	 * when we have never seen them finish a lap, which is a real state
	 * (lap 1, or a car that joined mid-session) and not a zero.
	 *
	 * `timeScale` is race seconds per envelope second: 1 live, the replay
	 * speed in a replay. A replay paces its envelopes `speed` times faster
	 * than the race ran, so without it every car at 50x sat two percent into
	 * its lap and the whole field piled up at the start/finish line. */
	fractionAt(driver: string, atMs: number, timeScale = 1): number | null {
		const entry = this.drivers.get(driver);
		if (!entry || entry.lastLapEndMs === null || entry.lapTimeMs === null) return null;
		const elapsed = (atMs - entry.lastLapEndMs) * (timeScale > 0 ? timeScale : 1);
		if (elapsed < 0) return 0;
		// Clamp rather than wrap: past one full lap our estimate has expired,
		// and parking the car at the line is visibly honest where silently
		// starting another lap would invent a lap that never happened.
		return Math.min(elapsed / entry.lapTimeMs, 0.999);
	}

	inPit(driver: string): boolean {
		return this.drivers.get(driver)?.inPit ?? false;
	}
}

/** A reusable arc-length table for one outline. */
export function outlineTable(outline: TrackOutline): ArcLengthTable {
	return buildArcLengthTable(outline.points);
}

/** Estimated position samples, one per driver we can place. */
export function syntheticPositions(
	tracker: LapProgressTracker,
	table: ArcLengthTable,
	atMs: number,
	drivers: readonly string[] = tracker.known
): Map<string, PositionSample> {
	const out = new Map<string, PositionSample>();
	for (const driver of drivers) {
		const fraction = tracker.fractionAt(driver, atMs);
		if (fraction === null) continue;
		const [x, y] = pointAtArcLength(table, fraction);
		out.set(driver, { x, y, tsMs: atMs });
	}
	return out;
}
