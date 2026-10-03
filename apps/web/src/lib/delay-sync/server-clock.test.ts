import { afterEach, describe, expect, it } from 'vitest';
import {
	bestOffset,
	measureServerOffset,
	NEGLIGIBLE_OFFSET_MS,
	offsetFromSample,
	serverNowMs,
	setServerClockOffsetMs
} from './server-clock';

afterEach(() => setServerClockOffsetMs(0));

describe('offsetFromSample', () => {
	it('assumes the server read its clock halfway through the round trip', () => {
		// Device says 1000 -> 1200; the server answered 61_100 (a minute ahead).
		expect(offsetFromSample({ sentMs: 1000, receivedMs: 1200, serverMs: 61_100 })).toBe(60_000);
	});
});

describe('bestOffset', () => {
	it('trusts the shortest round trip', () => {
		const offset = bestOffset([
			{ sentMs: 0, receivedMs: 2000, serverMs: 30_000 }, // 2 s trip: offset 29 s
			{ sentMs: 5000, receivedMs: 5100, serverMs: 35_050 } // 100 ms trip: offset 30 s
		]);
		expect(offset).toBe(30_000);
	});

	it('leaves a clock that is already right alone', () => {
		const offset = bestOffset([
			{ sentMs: 0, receivedMs: 100, serverMs: 50 + NEGLIGIBLE_OFFSET_MS - 1 }
		]);
		expect(offset).toBe(0);
	});

	it('corrects a device clock that runs FAST (the spoiler case)', () => {
		// Device 45 s ahead of the server: events would release 45 s early.
		expect(bestOffset([{ sentMs: 100_000, receivedMs: 100_080, serverMs: 55_040 }])).toBe(-45_000);
	});

	it('is 0 with no samples', () => {
		expect(bestOffset([])).toBe(0);
	});
});

describe('measureServerOffset', () => {
	it('measures a slow device clock end to end', async () => {
		let device = 1_000_000;
		const now = () => device;
		const offset = await measureServerOffset(
			async () => {
				device += 40; // the request's flight out
				const server = device + 120_000; // server two minutes ahead
				device += 40; // and back
				return server;
			},
			{ now }
		);
		expect(offset).toBe(120_000);
	});

	it('falls back to no correction when every request fails', async () => {
		const offset = await measureServerOffset(async () => {
			throw new Error('offline');
		});
		expect(offset).toBe(0);
	});

	it('skips a failed sample and uses the rest', async () => {
		let calls = 0;
		let device = 0;
		const offset = await measureServerOffset(
			async () => {
				calls++;
				if (calls === 1) throw new Error('lost');
				device += 10;
				const server = device + 5_000;
				device += 10;
				return server;
			},
			{ now: () => device }
		);
		expect(offset).toBe(5_000);
	});
});

describe('serverNowMs', () => {
	it('is the device clock plus the measured correction', () => {
		setServerClockOffsetMs(-30_000);
		const before = Date.now();
		const corrected = serverNowMs();
		expect(corrected).toBeGreaterThanOrEqual(before - 30_000);
		expect(corrected).toBeLessThanOrEqual(Date.now() - 30_000);
	});
});
