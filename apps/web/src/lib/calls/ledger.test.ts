import { describe, expect, it } from 'vitest';
import { eventTimeMs, TimelineBuffer, type Envelope } from '$lib/delay-sync';
import { CallLedgerReducer, scorecardOf } from './ledger';
import { CALL_KIND, callFromEnvelope, type CallOut, type CallStatus } from './view';

const BASE_MS = Date.parse('2026-03-05T14:00:00.000Z');

function call(id: string, status: CallStatus, confidence: CallOut['confidence'] = 'high'): CallOut {
	return {
		call_id: id,
		kind: 'undercut',
		driver: '4',
		rival: '63',
		decision_lap: 11,
		in_lap: 12,
		confidence,
		status,
		detail: status === 'pending' ? 'waiting_for_rival_stop' : null,
		window: 'open',
		crossover_lap: 14,
		projected_position: null,
		projected_margin_s: null,
		rival_stop_lap: status === 'pending' ? null : 15,
		driver_ahead_after: status === 'right' ? true : status === 'wrong' ? false : null,
		actual_position: null,
		actual_car_ahead: null
	};
}

function envelope(offsetMs: number, payload: CallOut): Envelope {
	return {
		event_time_utc: new Date(BASE_MS + offsetMs).toISOString(),
		kind: CALL_KIND,
		payload: { ...payload }
	};
}

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

describe('callFromEnvelope', () => {
	it('reads a call envelope and rejects anything else', () => {
		const out = call('undercut:4:63:12', 'right');
		expect(callFromEnvelope(envelope(0, out))).toEqual(out);
		expect(callFromEnvelope({ ...envelope(0, out), kind: 'lap_completed' })).toBeNull();
		expect(
			callFromEnvelope({ ...envelope(0, out), payload: { ...out, status: 'maybe' } })
		).toBeNull();
	});
});

describe('CallLedgerReducer', () => {
	it('replaces a call in place when its status changes', () => {
		const reducer = new CallLedgerReducer();
		const log = [
			envelope(0, call('a', 'pending')),
			envelope(1, call('b', 'pending', 'low')),
			envelope(2, call('a', 'wrong'))
		];

		reducer.update(log.slice(0, 2));
		expect(reducer.calls.map((c) => [c.call_id, c.status])).toEqual([
			['a', 'pending'],
			['b', 'pending']
		]);
		reducer.update(log);
		expect(reducer.calls.map((c) => [c.call_id, c.status])).toEqual([
			['a', 'wrong'],
			['b', 'pending']
		]);
		expect(reducer.scorecard).toMatchObject({ right: 0, wrong: 1, pending: 1 });
	});

	it('counts only right and wrong, split by confidence', () => {
		const card = scorecardOf([
			call('a', 'right', 'high'),
			call('b', 'right', 'high'),
			call('c', 'wrong', 'medium'),
			call('d', 'void', 'high'),
			call('e', 'no_call', 'low'),
			call('f', 'pending', 'low')
		]);
		expect(card).toEqual({
			right: 2,
			wrong: 1,
			byConfidence: [
				{ confidence: 'high', right: 2, wrong: 0 },
				{ confidence: 'medium', right: 0, wrong: 1 },
				{ confidence: 'low', right: 0, wrong: 0 }
			],
			pending: 1,
			void: 1,
			noCall: 1
		});
	});

	it('starts over when the released log is replaced by a shorter one', () => {
		const reducer = new CallLedgerReducer();
		reducer.update([envelope(0, call('a', 'right')), envelope(1, call('b', 'right'))]);
		reducer.update([envelope(0, call('c', 'pending'))]);
		expect(reducer.calls.map((c) => c.call_id)).toEqual(['c']);
	});
});

describe('the scorecard never runs ahead of the playback clock', () => {
	// Random races of calls: each locks pending at a random moment and
	// (most) resolve later. Envelopes arrive in random order; the clock is
	// released at random, non-monotonic moments. After every release, the
	// ledger must hold exactly the calls, with exactly the outcomes, whose
	// envelopes the clock's high-water mark has reached.
	it('holds over 200 seeded trials', () => {
		const rand = mulberry32(0x5c0e);
		const outcomes: CallStatus[] = ['right', 'wrong', 'void', 'no_call'];
		for (let trial = 0; trial < 200; trial++) {
			const sent: Envelope[] = [];
			const n = 1 + Math.floor(rand() * 12);
			for (let i = 0; i < n; i++) {
				const lock = Math.floor(rand() * 600_000);
				const confidence = (['high', 'medium', 'low'] as const)[Math.floor(rand() * 3)]!;
				sent.push(envelope(lock, call(`c${i}`, 'pending', confidence)));
				if (rand() < 0.8) {
					const status = outcomes[Math.floor(rand() * outcomes.length)]!;
					const settle = lock + 1 + Math.floor(rand() * 300_000);
					sent.push(envelope(settle, call(`c${i}`, status, confidence)));
				}
			}
			const buffer = new TimelineBuffer();
			for (const e of [...sent].sort(() => rand() - 0.5)) buffer.ingest(e);

			const reducer = new CallLedgerReducer();
			const log: Envelope[] = [];
			let highWater = -Infinity;
			for (let step = 0; step < 15; step++) {
				const clock = BASE_MS + Math.floor(rand() * 1_000_000);
				highWater = Math.max(highWater, clock);
				log.push(...buffer.release(clock));
				reducer.update(log);

				const due = sent
					.filter((e) => eventTimeMs(e) <= highWater)
					.sort((a, b) => eventTimeMs(a) - eventTimeMs(b));
				const expected = new Map<string, CallStatus>();
				for (const e of due)
					expected.set(String(e.payload.call_id), e.payload.status as CallStatus);
				expect(new Map(reducer.calls.map((c) => [c.call_id, c.status]))).toEqual(expected);
				const card = reducer.scorecard;
				expect(card.right).toBe([...expected.values()].filter((s) => s === 'right').length);
				expect(card.wrong).toBe([...expected.values()].filter((s) => s === 'wrong').length);
			}
		}
	});
});
