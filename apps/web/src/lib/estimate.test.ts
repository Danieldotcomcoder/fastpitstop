import { describe, expect, it } from 'vitest';
import {
	bandOpacity,
	bandWidthFraction,
	hasEnoughSamples,
	MIN_CLEAN_SAMPLES
} from './estimate';

describe('estimate band helpers', () => {
	it('refuses to render below the clean-lap floor', () => {
		expect(hasEnoughSamples(MIN_CLEAN_SAMPLES - 1)).toBe(false);
		expect(hasEnoughSamples(MIN_CLEAN_SAMPLES)).toBe(true);
	});

	it('makes opacity climb with sample support', () => {
		const thin = bandOpacity(5);
		const rich = bandOpacity(40);
		expect(thin).toBeLessThan(rich);
		expect(rich).toBe(1);
	});

	it('encodes a tighter CI as a narrower band', () => {
		const tight = bandWidthFraction(0.04, 0.06, 0.05);
		const wide = bandWidthFraction(-0.2, 0.4, 0.05);
		expect(tight).toBeLessThan(wide);
	});
});
