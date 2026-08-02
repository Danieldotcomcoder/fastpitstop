import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import {
	FALLBACK_TEXT,
	anomalyKindLabel,
	evidenceKindLabel,
	explanationViewFromApi,
	explanationViewFromEnvelope
} from './view';

function explanationEnvelope(overrides: Partial<Envelope['payload']> = {}): Envelope {
	return {
		event_time_utc: '2024-03-02T15:10:00Z',
		kind: 'explanation',
		payload: {
			anomaly_id: '2024-1-race~position_change:17:1,44:1709393113',
			kind: 'position_change',
			lap_number: 17,
			drivers: ['1', '44'],
			text: 'Race control confirms an on-track incident at this moment.',
			confidence: 'medium',
			grounded: true,
			cited_evidence: [
				{ evidence_id: 'race_control:abc', kind: 'race_control', href: null }
			],
			...overrides
		}
	};
}

describe('explanationViewFromEnvelope', () => {
	it('parses a grounded explanation envelope', () => {
		const view = explanationViewFromEnvelope(explanationEnvelope());
		expect(view).not.toBeNull();
		expect(view!.anomalyId).toContain('position_change');
		expect(view!.grounded).toBe(true);
		expect(view!.citedEvidence).toHaveLength(1);
		expect(view!.citedEvidence[0]!.kind).toBe('race_control');
	});

	it('returns null for a malformed payload', () => {
		expect(
			explanationViewFromEnvelope({
				event_time_utc: '2024-03-02T15:10:00Z',
				kind: 'explanation',
				payload: { kind: 'pace_loss' }
			})
		).toBeNull();
	});

	it('defaults an unknown confidence to low', () => {
		const view = explanationViewFromEnvelope(explanationEnvelope({ confidence: 'wat' }));
		expect(view!.confidence).toBe('low');
	});
});

describe('explanationViewFromApi', () => {
	it('maps REST ExplanationOut fields into the shared view shape', () => {
		const view = explanationViewFromApi({
			anomaly_id: 'id',
			kind: 'pace_loss',
			lap_number: 8,
			drivers: ['44'],
			anomaly_time: '2024-03-02T15:05:00Z',
			text: FALLBACK_TEXT,
			confidence: 'low',
			grounded: false,
			fallback_reason: 'low_evidence',
			cited_evidence: []
		});
		expect(view.grounded).toBe(false);
		expect(view.text).toBe(FALLBACK_TEXT);
		expect(view.citedEvidence).toEqual([]);
	});
});

describe('labels', () => {
	it('maps known anomaly and evidence kinds', () => {
		expect(anomalyKindLabel('position_change')).toBe('Position change');
		expect(evidenceKindLabel('radio_clip')).toBe('Team radio');
	});

	it('title-cases unknown kinds rather than hiding them', () => {
		expect(anomalyKindLabel('mystery_kind')).toBe('Mystery kind');
	});
});
