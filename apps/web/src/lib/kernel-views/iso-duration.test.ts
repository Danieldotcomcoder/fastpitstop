import { describe, expect, it } from 'vitest';
import { parseIsoDurationSeconds } from './iso-duration';

describe('parseIsoDurationSeconds', () => {
	it('parses plain seconds, including fractional', () => {
		expect(parseIsoDurationSeconds('PT1.234S')).toBeCloseTo(1.234, 6);
		expect(parseIsoDurationSeconds('PT0S')).toBe(0);
	});

	it('parses minutes and seconds combined', () => {
		expect(parseIsoDurationSeconds('PT1M2.5S')).toBeCloseTo(62.5, 6);
	});

	it('parses hours, minutes and seconds combined', () => {
		expect(parseIsoDurationSeconds('PT1H2M3S')).toBeCloseTo(3723, 6);
	});

	it('parses a negative duration', () => {
		expect(parseIsoDurationSeconds('-PT0.5S')).toBeCloseTo(-0.5, 6);
	});

	it('returns null for non-string input', () => {
		expect(parseIsoDurationSeconds(null)).toBeNull();
		expect(parseIsoDurationSeconds(undefined)).toBeNull();
		expect(parseIsoDurationSeconds(42)).toBeNull();
	});

	it('returns null for an unparseable string', () => {
		expect(parseIsoDurationSeconds('not a duration')).toBeNull();
		expect(parseIsoDurationSeconds('P')).toBeNull();
	});
});
