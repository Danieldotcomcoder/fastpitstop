import { describe, expect, it } from 'vitest';
import { buildStoryMoments, storyMomentRevealed } from './story';
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

describe('storyMomentRevealed', () => {
	const safetyCar = buildStoryMoments(
		[
			{
				anomaly_id: 'sc',
				kind: 'neutralization',
				lap_number: 28,
				drivers: ['4', '1'],
				anomaly_time: '2024-05-05T20:57:00Z',
				magnitude: 5,
				salience: 0.7,
				// Began on lap 28, but describes everything to the restart lap.
				summary: { start_lap: 28, end_lap: 32, reveal_driver: '4', reveal_lap: 33 },
				reasons: []
			}
		],
		new Map()
	)[0]!;

	it('waits for the reveal lap, not the lap the moment is numbered with', () => {
		const completedTo = (lastLap: number) => (_driver: string, lap: number) => lap <= lastLap;
		expect(storyMomentRevealed(safetyCar, completedTo(28), false)).toBe(false);
		expect(storyMomentRevealed(safetyCar, completedTo(32), false)).toBe(false);
		expect(storyMomentRevealed(safetyCar, completedTo(33), false)).toBe(true);
	});

	it('checks the reveal driver, not any driver on the card', () => {
		const onlyDriverOne = (driver: string, lap: number) => driver === '1' && lap <= 40;
		expect(storyMomentRevealed(safetyCar, onlyDriverOne, false)).toBe(false);
	});

	it('falls back to the moment lap for an older server', () => {
		const [legacy] = buildStoryMoments([ANOMALIES[1]!], new Map());
		expect(legacy!.revealLap).toBeNull();
		expect(storyMomentRevealed(legacy!, (_d, lap) => lap <= 8, false)).toBe(true);
		expect(storyMomentRevealed(legacy!, (_d, lap) => lap <= 7, false)).toBe(false);
	});
});
