import { describe, expect, it } from 'vitest';
import { buildStoryMoments } from './story';
import type { ExplanationView } from './view';
import { FALLBACK_TEXT } from './view';

const ANOMALIES = [
	{
		anomaly_id: 'a',
		kind: 'undercut_resolved',
		lap_number: 20,
		drivers: ['1', '44'],
		anomaly_time: '2024-03-02T15:20:00Z',
		magnitude: 1.2,
		salience: 0.8,
		summary: {},
		reasons: []
	},
	{
		anomaly_id: 'b',
		kind: 'pace_loss',
		lap_number: 8,
		drivers: ['44'],
		anomaly_time: '2024-03-02T15:05:00Z',
		magnitude: 0.4,
		salience: 0.3,
		summary: {},
		reasons: ['thin fit']
	}
];

function explanation(id: string, grounded: boolean): ExplanationView {
	return {
		anomalyId: id,
		kind: 'pace_loss',
		lapNumber: 8,
		drivers: ['44'],
		anomalyTime: '2024-03-02T15:05:00Z',
		text: grounded ? 'cited cause' : FALLBACK_TEXT,
		confidence: grounded ? 'high' : 'low',
		grounded,
		fallbackReason: grounded ? null : 'low_evidence',
		citedEvidence: []
	};
}

describe('buildStoryMoments', () => {
	it('keeps salience order and attaches fetched explanations', () => {
		const map = new Map([['a', explanation('a', true)]]);
		const moments = buildStoryMoments(ANOMALIES, map);
		expect(moments.map((m) => m.anomalyId)).toEqual(['a', 'b']);
		expect(moments[0]!.explanation?.grounded).toBe(true);
		expect(moments[1]!.explanation).toBeNull();
	});
});
