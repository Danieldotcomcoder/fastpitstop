/** Folds the visible domain-event log into per-driver marker state — the
 * "per-domain-event reducer" `$lib/delay-sync/visible-state.ts` explicitly
 * leaves to "the actual race views' job." Reads ONLY `Envelope`s that
 * `VisibleSessionState.events` already contains, i.e. already spoiler-safe;
 * this module never touches a clock or a delay itself. */
import type { Envelope } from '$lib/delay-sync';
import type { CarMarker } from './types';

export interface RaceState {
	cars: Map<string, CarMarker>;
}

export function createRaceState(): RaceState {
	return { cars: new Map() };
}

function markerOrDefault(state: RaceState, driver: string): CarMarker {
	const existing = state.cars.get(driver);
	if (existing) return existing;
	const created: CarMarker = {
		driver,
		code: null,
		team: null,
		compound: null,
		current: null,
		previous: null
	};
	state.cars.set(driver, created);
	return created;
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}

function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Mutates `state` in place with one newly-released envelope. Mutating
 * (rather than returning a fresh copy) is deliberate: a race has up to ~20
 * cars and this runs once per released envelope over a whole session — an
 * immutable copy-on-write map would cost far more than it buys here, and
 * nothing needs referential-equality change detection on `RaceState` itself
 * (the renderer re-reads it every animation frame regardless). */
export function applyEnvelope(state: RaceState, envelope: Envelope, tsMs: number): void {
	const payload = envelope.payload;
	switch (envelope.kind) {
		case 'car_position': {
			const driver = asString(payload.driver);
			const x = asNumber(payload.x);
			const y = asNumber(payload.y);
			if (driver === null || x === null || y === null) return;
			const marker = markerOrDefault(state, driver);
			if (marker.current !== null && tsMs <= marker.current.tsMs) return;
			marker.previous = marker.current;
			marker.current = { x, y, tsMs };
			return;
		}
		case 'driver_list_updated': {
			const drivers = payload.drivers;
			if (!Array.isArray(drivers)) return;
			for (const ref of drivers) {
				if (typeof ref !== 'object' || ref === null) continue;
				const record = ref as Record<string, unknown>;
				const number = asString(record.number);
				if (number === null) continue;
				const marker = markerOrDefault(state, number);
				const code = asString(record.code);
				const team = asString(record.team);
				if (code !== null) marker.code = code;
				if (team !== null) marker.team = team;
			}
			return;
		}
		case 'lap_completed':
		case 'stint_started': {
			const driver = asString(payload.driver);
			const compound = asString(payload.compound);
			if (driver === null || compound === null) return;
			markerOrDefault(state, driver).compound = compound;
			return;
		}
		default:
			return;
	}
}
