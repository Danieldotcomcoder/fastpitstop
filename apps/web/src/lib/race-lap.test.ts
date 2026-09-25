import { describe, expect, it } from 'vitest';
import { raceLap } from './race-lap';
import type { Envelope } from '$lib/delay-sync';

const env = (kind: string, payload: Record<string, unknown>): Envelope => ({
	event_time_utc: '2026-09-13T13:30:00Z',
	kind,
	payload
});

describe('raceLap', () => {
	it('falls back to completed laps when the feed has not said', () => {
		expect(raceLap([], 12)).toEqual({ current: 12, total: null, fromFeed: false });
	});

	it("prefers the feed's own counter over the completed-lap inference", () => {
		// The 2026 Spanish GP symptom: the race is on lap 21, but only lap 18
		// has been fully ingested for every driver.
		const events = [env('lap_counter_updated', { current_lap: 21, total_laps: 57 })];
		expect(raceLap(events, 18)).toEqual({ current: 21, total: 57, fromFeed: true });
	});

	it('takes the latest counter, not the first', () => {
		const events = [
			env('lap_counter_updated', { current_lap: 3, total_laps: 57 }),
			env('lap_counter_updated', { current_lap: 9, total_laps: 57 })
		];
		expect(raceLap(events, 0).current).toBe(9);
	});

	it('never goes backwards behind a completed lap', () => {
		const events = [env('lap_counter_updated', { current_lap: 4 })];
		expect(raceLap(events, 7).current).toBe(7);
	});

	it('ignores unrelated envelopes and malformed payloads', () => {
		const events = [
			env('gap_updated', { current_lap: 99 }),
			env('lap_counter_updated', { current_lap: 'twelve' })
		];
		expect(raceLap(events, 5)).toEqual({ current: 5, total: null, fromFeed: false });
	});
});
