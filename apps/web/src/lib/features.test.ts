import { describe, expect, it } from 'vitest';
import { flagFromEnv } from './features';

describe('flagFromEnv', () => {
	it('keeps the fallback when the variable is unset', () => {
		expect(flagFromEnv(undefined, true)).toBe(true);
		expect(flagFromEnv(undefined, false)).toBe(false);
	});

	it('reads the common truthy and falsy spellings, case-insensitively', () => {
		for (const on of ['1', 'true', 'TRUE', 'on', 'Yes', ' true ']) {
			expect(flagFromEnv(on, false)).toBe(true);
		}
		for (const off of ['0', 'false', 'OFF', 'no']) {
			expect(flagFromEnv(off, true)).toBe(false);
		}
	});

	it('keeps the fallback for an unrecognised value rather than guessing', () => {
		// A typo must not silently flip a surface on or off.
		expect(flagFromEnv('maybe', false)).toBe(false);
		expect(flagFromEnv('', true)).toBe(true);
	});
});
