import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { RevealTracker } from './reveal';

function envelope(
	kind: string,
	payload: Record<string, unknown>,
	tsIso = '2024-01-01T00:00:00Z'
): Envelope {
	return { event_time_utc: tsIso, kind, payload };
}

describe('RevealTracker.isStintRevealed', () => {
	it('is false before anything has been observed (fails closed)', () => {
		const tracker = new RevealTracker();
		expect(tracker.isStintRevealed('44', 1)).toBe(false);
	});

	it('reveals a stint once a LATER stint has started for that driver', () => {
		const tracker = new RevealTracker();
		tracker.update([
			envelope('stint_started', { driver: '44', stint_number: 1 }),
			envelope('stint_started', { driver: '44', stint_number: 2 })
		]);
		expect(tracker.isStintRevealed('44', 1)).toBe(true);
		expect(tracker.isStintRevealed('44', 2)).toBe(false); // stint 2 is still open
	});

	it('does not reveal the current (not-yet-superseded) stint', () => {
		const tracker = new RevealTracker();
		tracker.update([envelope('stint_started', { driver: '44', stint_number: 1 })]);
		expect(tracker.isStintRevealed('44', 1)).toBe(false);
	});

	it('reveals the final open stint once the session finishes (session_status_changed)', () => {
		const tracker = new RevealTracker();
		tracker.update([
			envelope('stint_started', { driver: '44', stint_number: 1 }),
			envelope('session_status_changed', { status: 'Finished' })
		]);
		expect(tracker.isStintRevealed('44', 1)).toBe(true);
	});

	it('reveals the final open stint on a CHEQUERED race control message (the historical-replay path)', () => {
		const tracker = new RevealTracker();
		tracker.update([
			envelope('stint_started', { driver: '44', stint_number: 1 }),
			envelope('race_control_message', { flag: 'CHEQUERED' })
		]);
		expect(tracker.isStintRevealed('44', 1)).toBe(true);
	});

	it('does not leak one driver into another', () => {
		const tracker = new RevealTracker();
		tracker.update([
			envelope('stint_started', { driver: '44', stint_number: 1 }),
			envelope('stint_started', { driver: '44', stint_number: 2 }),
			envelope('stint_started', { driver: '1', stint_number: 1 })
		]);
		expect(tracker.isStintRevealed('44', 1)).toBe(true);
		expect(tracker.isStintRevealed('1', 1)).toBe(false);
	});

	it('only processes newly-appended events on each update (cursor discipline)', () => {
		const tracker = new RevealTracker();
		const first = [envelope('stint_started', { driver: '44', stint_number: 1 })];
		tracker.update(first);
		expect(tracker.isStintRevealed('44', 1)).toBe(false);

		const second = [...first, envelope('stint_started', { driver: '44', stint_number: 2 })];
		tracker.update(second);
		expect(tracker.isStintRevealed('44', 1)).toBe(true);
	});
});

describe('RevealTracker.isLapRevealed', () => {
	it('is false before that lap has been completed', () => {
		const tracker = new RevealTracker();
		tracker.update([envelope('lap_completed', { driver: '44', lap_number: 5 })]);
		expect(tracker.isLapRevealed('44', 6)).toBe(false);
		expect(tracker.isLapRevealed('44', 5)).toBe(true);
		expect(tracker.isLapRevealed('44', 3)).toBe(true);
	});

	it('fails closed for a driver never observed', () => {
		const tracker = new RevealTracker();
		expect(tracker.isLapRevealed('1', 1)).toBe(false);
	});
});

describe('RevealTracker.reset', () => {
	it('clears all accumulated state', () => {
		const tracker = new RevealTracker();
		tracker.update([
			envelope('stint_started', { driver: '44', stint_number: 1 }),
			envelope('stint_started', { driver: '44', stint_number: 2 }),
			envelope('lap_completed', { driver: '44', lap_number: 5 })
		]);
		tracker.reset();
		expect(tracker.isStintRevealed('44', 1)).toBe(false);
		expect(tracker.isLapRevealed('44', 1)).toBe(false);
	});
});
