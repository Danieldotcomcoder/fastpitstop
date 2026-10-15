import { describe, expect, it } from 'vitest';
import type { StoryMoment } from './story';
import { holdStory, phaseOf } from './story-view';

function m(
	id: string,
	kind: string,
	lap: number | null,
	revealLap: number | null = null
): StoryMoment {
	return {
		anomalyId: id,
		kind,
		lapNumber: lap,
		drivers: ['1'],
		anomalyTime: '2024-03-02T15:00:00Z',
		magnitude: 1,
		salience: 0.5,
		reasons: [],
		explanation: null,
		revealDriver: revealLap === null ? null : '1',
		revealLap
	};
}

const MOMENTS = [
	m('start', 'race_start', 1),
	m('pass', 'position_change', 7),
	m('sc', 'neutralization', 18, 21),
	m('undercut', 'undercut_resolved', 35),
	m('finish', 'race_finish', 57),
	m('nolap', 'retirement', null)
];

describe('holdStory', () => {
	it('shows only moments the viewer has reached, and counts the rest', () => {
		const held = holdStory(MOMENTS, 20);
		expect(held.shown.map((x) => x.anomalyId)).toEqual(['start', 'pass']);
		expect(held.heldBack).toBe(4);
		expect(held.groups.map((g) => g.title)).toEqual(['The start', 'On track']);
	});

	it('waits for a safety car until its restart lap', () => {
		expect(holdStory(MOMENTS, 20).shown.some((x) => x.anomalyId === 'sc')).toBe(false);
		expect(holdStory(MOMENTS, 21).shown.some((x) => x.anomalyId === 'sc')).toBe(true);
	});

	it('shows everything for the whole race, in phase groups', () => {
		const held = holdStory(MOMENTS, null);
		expect(held.heldBack).toBe(0);
		expect(held.groups.map((g) => g.id)).toEqual([
			'start',
			'track',
			'strategy',
			'incidents',
			'finish'
		]);
	});

	it('shows nothing past the start at lap 0', () => {
		expect(holdStory(MOMENTS, 0).shown).toEqual([]);
	});
});

describe('phaseOf', () => {
	it('files an unknown kind under incidents', () => {
		expect(phaseOf('something_new')).toBe('incidents');
	});
});
