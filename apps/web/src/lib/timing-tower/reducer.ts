/** Folds the visible domain-event log into the timing tower's per-driver
 * rows — reads ONLY `Envelope`s `VisibleSessionState.events` already
 * contains, so this needs no REST fetch and is spoiler-safe by construction,
 * the same "nothing here Delay Sync hasn't already released" reasoning
 * `$lib/kernel-views/gap-stream.ts` gives for skipping `RevealTracker`. */
import type { Envelope } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from '$lib/kernel-views';
import { createDriverRow, type DriverRow } from './types';

export interface TimingTowerState {
	rows: Map<string, DriverRow>;
}

export function createTimingTowerState(): TimingTowerState {
	return { rows: new Map() };
}

function rowOrDefault(state: TimingTowerState, driver: string): DriverRow {
	const existing = state.rows.get(driver);
	if (existing) return existing;
	const created = createDriverRow(driver);
	state.rows.set(driver, created);
	return created;
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function asBoolean(value: unknown): boolean | null {
	return typeof value === 'boolean' ? value : null;
}

/** Mutates `state` in place for one newly-released envelope — mirrors
 * `car-state.ts`/`gap-stream.ts`'s own reasoning for mutating rather than
 * copy-on-write (up to ~20 drivers, re-read every render regardless). */
export function applyTowerEnvelope(state: TimingTowerState, envelope: Envelope): void {
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
				const row = rowOrDefault(state, number);
				const code = asString(record.code);
				const team = asString(record.team);
				if (code !== null) row.code = code;
				if (team !== null) row.team = team;
			}
			return;
		}
		case 'position_changed': {
			const driver = asString(payload.driver);
			const position = asNumber(payload.position);
			if (driver === null) return;
			const row = rowOrDefault(state, driver);
			if (position !== null) row.position = position;
			return;
		}
		case 'gap_updated': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			const row = rowOrDefault(state, driver);
			row.gapToLeaderS = parseIsoDurationSeconds(payload.gap_to_leader);
			row.lapsDown = asNumber(payload.laps_down);
			row.intervalToAheadS = parseIsoDurationSeconds(payload.interval_to_ahead);
			return;
		}
		case 'lap_completed': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			const row = rowOrDefault(state, driver);
			const lapNumber = asNumber(payload.lap_number);
			if (lapNumber !== null) row.lapNumber = lapNumber;
			const lapTimeS = parseIsoDurationSeconds(payload.lap_time);
			if (lapTimeS !== null) row.lastLapTimeS = lapTimeS;
			const isPersonalBest = asBoolean(payload.is_personal_best);
			if (isPersonalBest !== null) row.isPersonalBest = isPersonalBest;
			const compound = asString(payload.compound);
			if (compound !== null) row.compound = compound;
			const tyreAge = asNumber(payload.tyre_age);
			if (tyreAge !== null) row.tyreAge = tyreAge;
			return;
		}
		case 'stint_started': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			const row = rowOrDefault(state, driver);
			const stintNumber = asNumber(payload.stint_number);
			if (stintNumber !== null) row.stintNumber = stintNumber;
			const compound = asString(payload.compound);
			if (compound !== null) row.compound = compound;
			const tyreAgeAtStart = asNumber(payload.tyre_age_at_start);
			if (tyreAgeAtStart !== null) row.tyreAge = tyreAgeAtStart;
			return;
		}
		case 'pit_entry': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			rowOrDefault(state, driver).inPit = true;
			return;
		}
		case 'pit_exit': {
			const driver = asString(payload.driver);
			if (driver === null) return;
			rowOrDefault(state, driver).inPit = false;
			return;
		}
		default:
			return;
	}
}

function driverNumberCompare(a: string, b: string): number {
	const na = Number(a);
	const nb = Number(b);
	if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
	return a.localeCompare(b);
}

/** Race order for display: classified position ascending, with drivers the
 * feed hasn't placed yet (`position === null`) sorted after every classified
 * one rather than treated as P0 — an unknown position is not a leader. */
export function sortedRows(state: TimingTowerState): DriverRow[] {
	return Array.from(state.rows.values()).sort((a, b) => {
		if (a.position !== null && b.position !== null) return a.position - b.position;
		if (a.position !== null) return -1;
		if (b.position !== null) return 1;
		return driverNumberCompare(a.driver, b.driver);
	});
}

/** Cursor-based fold over `VisibleSessionState.events` — the same
 * "only process what's newly appended" discipline `RaceStateReducer` and
 * `GapStreamReducer` use, since `events` only ever grows over a session. */
export class TimingTowerReducer {
	readonly state: TimingTowerState = createTimingTowerState();
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope === undefined) continue; // unreachable: i is always < events.length here
			applyTowerEnvelope(this.state, envelope);
		}
		this.cursor = events.length;
	}

	get rows(): DriverRow[] {
		return sortedRows(this.state);
	}

	reset(): void {
		this.state.rows.clear();
		this.cursor = 0;
	}
}
