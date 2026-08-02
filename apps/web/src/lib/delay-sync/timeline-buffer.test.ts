import { describe, expect, it } from 'vitest';
import type { Envelope } from './envelope';
import { eventTimeMs } from './envelope';
import { TimelineBuffer } from './timeline-buffer';

const BASE_MS = Date.parse('2026-03-05T14:00:00.000Z');

function envelopeAt(offsetMs: number, kind = 'lap_completed'): Envelope {
	return {
		event_time_utc: new Date(BASE_MS + offsetMs).toISOString(),
		kind,
		payload: { offsetMs }
	};
}

/** Deterministic PRNG (mulberry32) so the property test below is
 * reproducible across runs and CI machines — a "randomized" test that
 * cannot be reproduced on failure is worse than a fixed fixture. */
function mulberry32(seed: number): () => number {
	let a = seed;
	return () => {
		a |= 0;
		a = (a + 0x6d2b79f5) | 0;
		let t = Math.imul(a ^ (a >>> 15), 1 | a);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

function shuffle<T>(items: T[], rand: () => number): T[] {
	const copy = [...items];
	for (let i = copy.length - 1; i > 0; i--) {
		const j = Math.floor(rand() * (i + 1));
		const tmp = copy[i]!;
		copy[i] = copy[j]!;
		copy[j] = tmp!;
	}
	return copy;
}

describe('TimelineBuffer.release', () => {
	it('releases only envelopes at or before the given playback clock, in event-time order', () => {
		const buffer = new TimelineBuffer();
		buffer.ingest(envelopeAt(30_000));
		buffer.ingest(envelopeAt(10_000));
		buffer.ingest(envelopeAt(20_000));

		expect(buffer.release(BASE_MS + 15_000).map((e) => e.payload.offsetMs)).toEqual([10_000]);
		expect(buffer.release(BASE_MS + 25_000).map((e) => e.payload.offsetMs)).toEqual([20_000]);
		expect(buffer.release(BASE_MS + 25_000)).toEqual([]); // already released, not re-released
		expect(buffer.release(BASE_MS + 100_000).map((e) => e.payload.offsetMs)).toEqual([30_000]);
	});

	it('never releases an envelope whose event_time_utc is ahead of the clock', () => {
		const buffer = new TimelineBuffer();
		buffer.ingest(envelopeAt(60_000));
		expect(buffer.release(BASE_MS + 59_999)).toEqual([]);
		expect(buffer.pendingCount).toBe(1);
	});

	it('releases an envelope exactly at the clock boundary (event_time <= clock, not <)', () => {
		const buffer = new TimelineBuffer();
		buffer.ingest(envelopeAt(60_000));
		expect(buffer.release(BASE_MS + 60_000)).toHaveLength(1);
	});

	it('respects release order even when envelopes arrive out of order', () => {
		const buffer = new TimelineBuffer();
		for (const offset of [50_000, 10_000, 40_000, 20_000, 30_000]) {
			buffer.ingest(envelopeAt(offset));
		}
		const released = buffer.release(BASE_MS + 1_000_000);
		expect(released.map((e) => e.payload.offsetMs)).toEqual([
			10_000, 20_000, 30_000, 40_000, 50_000
		]);
	});

	// --- Property test: the whole spoiler-safety guarantee ---------------
	//
	// For a randomized envelope stream, random arrival order, and a random
	// delay, no envelope handed back by `release` may ever have an
	// event_time_utc after the clock it was released at. This is checked
	// over many seeded trials, each with its own random envelope count,
	// event-time spread, arrival order and clock-tick schedule.
	it('property: zero leaks — no released envelope ever has event_time > the release clock', () => {
		const TRIALS = 200;
		const rand = mulberry32(0xc0ffee);

		for (let trial = 0; trial < TRIALS; trial++) {
			const n = 1 + Math.floor(rand() * 60);
			const envelopes: Envelope[] = [];
			for (let i = 0; i < n; i++) {
				const offsetMs = Math.floor(rand() * 600_000); // spread over 10 minutes
				const kind = rand() < 0.15 ? 'kernel_snapshot' : 'lap_completed';
				envelopes.push(envelopeAt(offsetMs, kind));
			}
			const arrivalOrder = shuffle(envelopes, rand);

			const buffer = new TimelineBuffer();
			for (const envelope of arrivalOrder) buffer.ingest(envelope);

			// A random schedule of clock ticks, not necessarily monotonic —
			// the guarantee must hold even if a caller calls release() with a
			// clock that doesn't strictly increase.
			const allReleased: Envelope[] = [];
			for (let tick = 0; tick < 12; tick++) {
				const clockMs = Math.floor(rand() * 700_000);
				const released = buffer.release(BASE_MS + clockMs);
				for (const envelope of released) {
					expect(eventTimeMs(envelope)).toBeLessThanOrEqual(BASE_MS + clockMs);
				}
				allReleased.push(...released);
			}

			// Every envelope is released at most once across the whole run.
			expect(new Set(allReleased).size).toBe(allReleased.length);
		}
	});

	it('determinism: the same ingest + release(clock) sequence always yields the same result', () => {
		const rand = mulberry32(42);
		const offsets = Array.from({ length: 40 }, () => Math.floor(rand() * 500_000));
		const clockSchedule = [50_000, 120_000, 90_000, 500_000, 10_000, 250_000];

		function run(): Envelope[][] {
			const buffer = new TimelineBuffer();
			for (const offset of offsets) buffer.ingest(envelopeAt(offset));
			return clockSchedule.map((clockMs) => buffer.release(BASE_MS + clockMs));
		}

		const first = run();
		const second = run();
		expect(second).toEqual(first);
	});
});
