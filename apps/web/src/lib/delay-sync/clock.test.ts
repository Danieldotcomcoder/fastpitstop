import { describe, expect, it } from 'vitest';
import { clamp, playbackClockMs } from './clock';

describe('playbackClockMs', () => {
	it('subtracts the delay (in seconds, converted to ms) from wall time', () => {
		expect(playbackClockMs(1_000_000, 30)).toBe(1_000_000 - 30_000);
	});

	it('is a no-op at zero delay', () => {
		expect(playbackClockMs(1_000_000, 0)).toBe(1_000_000);
	});
});

describe('clamp', () => {
	it('clamps below the minimum', () => {
		expect(clamp(-10, 0, 120)).toBe(0);
	});

	it('clamps above the maximum', () => {
		expect(clamp(999, 0, 120)).toBe(120);
	});

	it('passes through in-range values unchanged', () => {
		expect(clamp(45, 0, 120)).toBe(45);
	});

	it('rejects an inverted range', () => {
		expect(() => clamp(5, 100, 0)).toThrow();
	});
});
