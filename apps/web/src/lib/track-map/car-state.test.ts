import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { applyEnvelope, createRaceState } from './car-state';

function envelope(kind: string, payload: Record<string, unknown>): Envelope {
	return { event_time_utc: new Date(0).toISOString(), kind, payload };
}

describe('applyEnvelope: car_position', () => {
	it('creates a marker on the first sample with previous null', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 1, y: 2 }), 1000);

		const marker = state.cars.get('44');
		expect(marker?.current).toEqual({ x: 1, y: 2, tsMs: 1000 });
		expect(marker?.previous).toBeNull();
	});

	it('shifts current into previous on a later sample', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 1, y: 2 }), 1000);
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 3, y: 4 }), 1200);

		const marker = state.cars.get('44');
		expect(marker?.previous).toEqual({ x: 1, y: 2, tsMs: 1000 });
		expect(marker?.current).toEqual({ x: 3, y: 4, tsMs: 1200 });
	});

	it('ignores an out-of-order or duplicate sample', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 1, y: 2 }), 1000);
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 99, y: 99 }), 900);

		const marker = state.cars.get('44');
		expect(marker?.current).toEqual({ x: 1, y: 2, tsMs: 1000 });
		expect(marker?.previous).toBeNull();
	});

	it('ignores a payload missing numeric coordinates', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: null, y: 2 }), 1000);
		expect(state.cars.has('44')).toBe(false);
	});

	it('tracks multiple drivers independently', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 1, y: 1 }), 1000);
		applyEnvelope(state, envelope('car_position', { driver: '1', x: 2, y: 2 }), 1000);
		expect(state.cars.size).toBe(2);
	});
});

describe('applyEnvelope: driver_list_updated', () => {
	it('sets code and team, creating a marker with no position yet', () => {
		const state = createRaceState();
		applyEnvelope(
			state,
			envelope('driver_list_updated', {
				drivers: [{ number: '44', code: 'HAM', team: 'Mercedes' }]
			}),
			1000
		);

		const marker = state.cars.get('44');
		expect(marker?.code).toBe('HAM');
		expect(marker?.team).toBe('Mercedes');
		expect(marker?.current).toBeNull();
	});

	it('merges onto an existing marker without clobbering its position', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('car_position', { driver: '44', x: 1, y: 1 }), 1000);
		applyEnvelope(
			state,
			envelope('driver_list_updated', { drivers: [{ number: '44', code: 'HAM' }] }),
			1000
		);

		const marker = state.cars.get('44');
		expect(marker?.code).toBe('HAM');
		expect(marker?.current).toEqual({ x: 1, y: 1, tsMs: 1000 });
	});

	it('ignores malformed driver entries', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('driver_list_updated', { drivers: [{ code: 'HAM' }] }), 1000);
		expect(state.cars.size).toBe(0);
	});
});

describe('applyEnvelope: compound updates', () => {
	it.each(['lap_completed', 'stint_started'])('%s sets the driver compound', (kind) => {
		const state = createRaceState();
		applyEnvelope(state, envelope(kind, { driver: '44', compound: 'soft' }), 1000);
		expect(state.cars.get('44')?.compound).toBe('soft');
	});

	it('ignores an unrelated event kind', () => {
		const state = createRaceState();
		applyEnvelope(state, envelope('weather_sample', { air_temp: 25 }), 1000);
		expect(state.cars.size).toBe(0);
	});
});
