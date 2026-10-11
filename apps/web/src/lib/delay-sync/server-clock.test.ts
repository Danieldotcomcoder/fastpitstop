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
	it('is a lower bound: the server cannot have read its clock after the reply arrived', () => {
		// Device says 1000 -> 1200; the server answered 61_100 (a minute ahead).
		expect(offsetFromSample({ sentMs: 1000, receivedMs: 1200, serverMs: 61_100 })).toBe(59_900);
	});
});

describe('bestOffset', () => {
	it('keeps the tightest lower bound', () => {
		const offset = bestOffset([
			{ sentMs: 0, receivedMs: 2000, serverMs: 30_000 }, // 2 s trip: at least 28 s
			{ sentMs: 5000, receivedMs: 5100, serverMs: 35_050 } // 100 ms trip: at least 29.95 s
		]);
		expect(offset).toBe(29_950);
	});

	it('never moves the clock ahead when a busy server answers late (the spoiler bug)', () => {
		// Device and server agree, but the server only read its clock 3 s
		// into a 3.02 s round trip. The old midpoint read that as the server
		// 1.5 s ahead and released moments 1.5 s early.
		const offset = bestOffset([{ sentMs: 0, receivedMs: 3020, serverMs: 3010 }]);
		expect(offset).toBeLessThanOrEqual(0);
	});

	it('leaves a clock that is already right alone', () => {
		const offset = bestOffset([
			{ sentMs: 0, receivedMs: 100, serverMs: 50 + NEGLIGIBLE_OFFSET_MS - 1 }
		]);
		expect(offset).toBe(0);
	});

	it('corrects a device clock that runs FAST (the spoiler case)', () => {
		// Device 45 s ahead of the server: events would release 45 s early.
		expect(bestOffset([{ sentMs: 100_000, receivedMs: 100_080, serverMs: 55_040 }])).toBe(-45_040);
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
		expect(offset).toBe(119_960);
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
		expect(offset).toBe(4_990);
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
