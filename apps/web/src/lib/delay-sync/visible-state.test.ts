import { describe, expect, it } from 'vitest';
import type { Envelope } from './envelope';
import {
	applyReleasedEnvelope,
	applyReleasedEnvelopes,
	EMPTY_VISIBLE_STATE,
	POSITION_FRAME_WINDOW
} from './visible-state';

function domainEnvelope(iso: string, kind = 'lap_completed'): Envelope {
	return { event_time_utc: iso, kind, payload: {} };
}

function snapshotEnvelope(iso: string, decisionLap: number): Envelope {
	return {
		event_time_utc: iso,
		kind: 'kernel_snapshot',
		payload: { decision_lap: decisionLap, crossovers: [] }
	};
}

function framesEnvelope(iso: string, x = 0): Envelope {
	return { event_time_utc: iso, kind: 'car_positions', payload: { cars: { '1': [x, 0] } } };
}

describe('applyReleasedEnvelope', () => {
	it('appends domain events to the event log', () => {
		const state = applyReleasedEnvelope(
			EMPTY_VISIBLE_STATE,
			domainEnvelope('2026-03-05T14:00:00Z')
		);
		expect(state.events).toHaveLength(1);
		expect(state.lastEventTimeUtc).toBe('2026-03-05T14:00:00Z');
		expect(state.latestSnapshot).toBeNull();
	});

	it('routes kernel_snapshot envelopes to latestSnapshot, not the event log', () => {
		const state = applyReleasedEnvelope(
			EMPTY_VISIBLE_STATE,
			snapshotEnvelope('2026-03-05T14:00:00Z', 7)
		);
		expect(state.events).toHaveLength(0);
		expect(state.latestSnapshot).toEqual({ decision_lap: 7, crossovers: [] });
	});

	it('never mutates the input state (pure reducer)', () => {
		const before = EMPTY_VISIBLE_STATE;
		applyReleasedEnvelope(before, domainEnvelope('2026-03-05T14:00:00Z'));
		expect(before).toEqual(EMPTY_VISIBLE_STATE);
		expect(before.events).toHaveLength(0);
	});
});

describe('applyReleasedEnvelopes', () => {
	it('folds a batch in order and keeps only the latest snapshot', () => {
		const state = applyReleasedEnvelopes(EMPTY_VISIBLE_STATE, [
			domainEnvelope('2026-03-05T14:00:00Z'),
			snapshotEnvelope('2026-03-05T14:00:01Z', 1),
			domainEnvelope('2026-03-05T14:00:02Z'),
			snapshotEnvelope('2026-03-05T14:00:03Z', 2)
		]);
		expect(state.events).toHaveLength(2);
		expect(state.latestSnapshot).toEqual({ decision_lap: 2, crossovers: [] });
		expect(state.lastEventTimeUtc).toBe('2026-03-05T14:00:03Z');
	});
});

describe('car_positions frames', () => {
	it('go to the position window, never the event log', () => {
		const state = applyReleasedEnvelopes(EMPTY_VISIBLE_STATE, [
			framesEnvelope('2026-03-05T14:00:00Z'),
			domainEnvelope('2026-03-05T14:00:00.100Z'),
			framesEnvelope('2026-03-05T14:00:00.250Z')
		]);
		expect(state.events.map((e) => e.kind)).toEqual(['lap_completed']);
		expect(state.positionFrames).toHaveLength(2);
		expect(state.lastEventTimeUtc).toBe('2026-03-05T14:00:00.250Z');

		const single = applyReleasedEnvelope(
			EMPTY_VISIBLE_STATE,
			framesEnvelope('2026-03-05T14:00:00Z')
		);
		expect(single.events).toHaveLength(0);
		expect(single.positionFrames).toHaveLength(1);
	});

	it('keep only the most recent window, oldest first', () => {
		const batch = Array.from({ length: POSITION_FRAME_WINDOW + 40 }, (_, i) =>
			framesEnvelope(new Date(Date.UTC(2026, 2, 5, 14) + i * 250).toISOString(), i)
		);
		const state = applyReleasedEnvelopes(EMPTY_VISIBLE_STATE, batch);
		expect(state.positionFrames).toHaveLength(POSITION_FRAME_WINDOW);
		expect(state.positionFrames[0]).toBe(batch[40]);
		expect(state.positionFrames.at(-1)).toBe(batch.at(-1));
	});
});
