import { describe, expect, it } from 'vitest';
import { delaySecondsFromTap } from './calibration';

describe('delaySecondsFromTap', () => {
	it('yields the correct offset for a simulated tap', () => {
		const eventTimeUtc = '2026-03-05T14:00:00.000Z';
		const tapWallMs = Date.parse(eventTimeUtc) + 47_000; // user's TV is 47s behind

		expect(delaySecondsFromTap(tapWallMs, eventTimeUtc)).toBeCloseTo(47, 9);
	});

	it('accepts an epoch-ms event time directly', () => {
		const eventMs = 1_000_000_000;
		expect(delaySecondsFromTap(eventMs + 30_000, eventMs)).toBeCloseTo(30, 9);
	});

	it('returns zero for a tap that lands exactly on the event', () => {
		const eventTimeUtc = '2026-03-05T14:00:00.000Z';
		expect(delaySecondsFromTap(Date.parse(eventTimeUtc), eventTimeUtc)).toBe(0);
	});

	it('returns a negative offset for a tap that lands before the event (caller clamps)', () => {
		const eventTimeUtc = '2026-03-05T14:00:00.000Z';
		const tapWallMs = Date.parse(eventTimeUtc) - 5_000;
		expect(delaySecondsFromTap(tapWallMs, eventTimeUtc)).toBeCloseTo(-5, 9);
	});

	it('throws on an unparseable event_time_utc', () => {
		expect(() => delaySecondsFromTap(Date.now(), 'not-a-date')).toThrow();
	});
});
