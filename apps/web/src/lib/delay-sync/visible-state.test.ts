import { describe, expect, it } from 'vitest';
import type { Envelope } from './envelope';
import {
	applyReleasedEnvelope,
	applyReleasedEnvelopes,
	EMPTY_VISIBLE_STATE
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
