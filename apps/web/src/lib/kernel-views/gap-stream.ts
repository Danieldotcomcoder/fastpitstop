/** Folds the visible domain-event log into per-driver gap-to-leader time
 * series — the data behind the "gap-to-leader stream graph" view. Reads
 * ONLY `Envelope`s `VisibleSessionState.events` already contains (the same
 * "per-domain-event reducer is the actual race views' job" split
 * `$lib/track-map/car-state.ts` follows), so this needs no REST fetch and
 * is spoiler-safe by construction — never gated by `$lib/kernel-views/reveal`,
 * because there is nothing here Delay Sync hasn't already released.
 *
 * Deliberately reads the raw `gap_updated` stream rather than `/laps`'
 * per-lap MEDIAN gap: the feed sends `gap_updated` many times per lap, and
 * that finer cadence is what makes the chart actually flow — a once-per-lap
 * median would just be a jagged staircase. */
import { type Envelope, eventTimeMs } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from './iso-duration';

export interface GapSample {
	/** The driver's own most recently completed lap as of this sample —
	 * `null` before their first lap completes. */
	lapNumber: number | null;
	tsMs: number;
	/** `null` when lapped (see `lapsDown`) or the feed sent no value. */
	gapToLeaderS: number | null;
	lapsDown: number | null;
}

export interface DriverGapSeries {
	driver: string;
	code: string | null;
	team: string | null;
	samples: GapSample[];
}

export interface GapStreamState {
	drivers: Map<string, DriverGapSeries>;
}

export function createGapStreamState(): GapStreamState {
	return { drivers: new Map() };
}

/** A generous safety valve, not a real-world limit: a full race's
 * `gap_updated` cadence sits well under this per driver. */
const MAX_SAMPLES_PER_DRIVER = 4000;

function seriesOrDefault(state: GapStreamState, driver: string): DriverGapSeries {
	const existing = state.drivers.get(driver);
	if (existing) return existing;
	const created: DriverGapSeries = { driver, code: null, team: null, samples: [] };
	state.drivers.set(driver, created);
	return created;
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Mutates `state`/`currentLap` in place for one newly-released envelope —
 * mirrors `car-state.ts`'s own reasoning for mutating rather than
 * copy-on-write (up to ~20 drivers, re-read every animation frame regardless). */
export function applyGapEnvelope(
	state: GapStreamState,
	envelope: Envelope,
	currentLap: Map<string, number>
): void {
	const payload = envelope.payload;
	switch (envelope.kind) {
		case 'driver_list_updated': {
			const drivers = payload.drivers;
			if (!Array.isArray(drivers)) return;
			for (const ref of drivers) {
				if (typeof ref !== 'object' || ref === null) continue;
				const record = ref as Record<string, unknown>;
				const number = asString(record.number);
				if (number === null) continue;
				const series = seriesOrDefault(state, number);
				const code = asString(record.code);
				const team = asString(record.team);
				if (code !== null) series.code = code;
				if (team !== null) series.team = team;
			}
			return;
		}
		case 'lap_completed': {
			const driver = asString(payload.driver);
			const lapNumber = asNumber(payload.lap_number);
			if (driver === null || lapNumber === null) return;
			seriesOrDefault(state, driver);
			currentLap.set(driver, lapNumber);
			return;
		}
		case 'gap_updated': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			const series = seriesOrDefault(state, driver);
			const gapToLeaderS = parseIsoDurationSeconds(payload.gap_to_leader);
			const lapsDown = asNumber(payload.laps_down);
			const lapNumber = currentLap.get(driver) ?? null;
			series.samples.push({ lapNumber, tsMs: eventTimeMs(envelope), gapToLeaderS, lapsDown });
			if (series.samples.length > MAX_SAMPLES_PER_DRIVER) series.samples.shift();
			return;
		}
		default:
			return;
	}
}

/** Cursor-based fold — only newly-appended `events` are processed on each
 * call, the same discipline `RaceStateReducer` uses. */
export class GapStreamReducer {
	readonly state: GapStreamState = createGapStreamState();
	private cursor = 0;
	private readonly currentLap = new Map<string, number>();

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue; // unreachable: i is always < events.length here
			applyGapEnvelope(this.state, envelope, this.currentLap);
		}
		this.cursor = events.length;
	}

	reset(): void {
		this.state.drivers.clear();
		this.currentLap.clear();
		this.cursor = 0;
	}
}
