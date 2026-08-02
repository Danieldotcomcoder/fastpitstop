import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { ExplanationLaneReducer } from './lane';

function explanationEnvelope(anomalyId: string, text: string): Envelope {
	return {
		event_time_utc: '2024-05-01T14:05:00Z',
		kind: 'explanation',
		payload: {
			anomaly_id: anomalyId,
			kind: 'pace_loss',
			lap_number: 8,
			drivers: ['44'],
			text,
			confidence: 'high',
			grounded: true,
			cited_evidence: []
		}
	};
}

function otherEnvelope(): Envelope {
	return { event_time_utc: '2024-05-01T14:00:00Z', kind: 'lap_completed', payload: { driver: '1' } };
}

describe('ExplanationLaneReducer', () => {
	it('ignores non-explanation envelopes', () => {
		const reducer = new ExplanationLaneReducer();
		reducer.update([otherEnvelope()]);
		expect(reducer.explanations).toEqual([]);
	});

	it('collects explanation envelopes in arrival order', () => {
		const reducer = new ExplanationLaneReducer();
		reducer.update([
			explanationEnvelope('a', 'first'),
			explanationEnvelope('b', 'second')
		]);
		expect(reducer.explanations.map((e) => e.anomalyId)).toEqual(['a', 'b']);
	});

	it('only processes newly-appended events on a later update', () => {
		const reducer = new ExplanationLaneReducer();
		const first = [explanationEnvelope('a', 'first')];
		reducer.update(first);
		reducer.update([...first, explanationEnvelope('b', 'second')]);
		expect(reducer.explanations.map((e) => e.anomalyId)).toEqual(['a', 'b']);
	});

	it('deduplicates by anomalyId', () => {
		const reducer = new ExplanationLaneReducer();
		const env = explanationEnvelope('a', 'first');
		reducer.update([env, env]);
		expect(reducer.explanations).toHaveLength(1);
	});

	it('reset clears accumulated explanations and the cursor', () => {
		const reducer = new ExplanationLaneReducer();
		reducer.update([explanationEnvelope('a', 'first')]);
		reducer.reset();
		expect(reducer.explanations).toEqual([]);
		reducer.update([explanationEnvelope('a', 'first')]);
		expect(reducer.explanations).toHaveLength(1);
	});

	it('returns a fresh array each explanations read (Svelte 5 $derived.by)', () => {
		const reducer = new ExplanationLaneReducer();
		reducer.update([explanationEnvelope('a', 'first')]);
		const first = reducer.explanations;
		const second = reducer.explanations;
		expect(first).toEqual(second);
		expect(first).not.toBe(second);
	});
});
