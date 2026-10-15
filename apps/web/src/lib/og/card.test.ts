import { describe, expect, it } from 'vitest';
import { nameLines, shareCardSvg } from './card';

const CARD = {
	season: 2026,
	round: 19,
	eventName: 'United States Grand Prix',
	venue: 'Circuit of the Americas',
	dates: '23–25 Oct',
	outline: [
		[0, 0],
		[10, 0],
		[10, 5],
		[0, 5]
	] as [number, number][]
};

describe('shareCardSvg', () => {
	it('names the Grand Prix, its round, venue and dates, and says it is unofficial', () => {
		const svg = shareCardSvg(CARD);
		for (const text of [
			'United States',
			'Grand Prix',
			'ROUND 19 · 2026',
			'Circuit of the Americas',
			'23–25 Oct',
			'unofficial, not associated with Formula 1'
		]) {
			expect(svg).toContain(text);
		}
		expect(svg).toContain('<path d="M');
	});

	it('draws no circuit when it has no outline, and escapes what it prints', () => {
		const svg = shareCardSvg({ ...CARD, outline: null, venue: 'A & B <Circuit>' });
		expect(svg).not.toContain('<path');
		expect(svg).toContain('A &amp; B &lt;Circuit&gt;');
	});

	it('carries nothing but the calendar: no result wording', () => {
		expect(shareCardSvg(CARD)).not.toMatch(/won|winner|victory|podium|pole|P1\b|fastest/i);
	});
});

describe('nameLines', () => {
	it('keeps a short name on one line and splits a long one evenly', () => {
		expect(nameLines('Monaco Grand Prix')).toEqual(['Monaco', 'Grand Prix']);
		expect(nameLines('Italian GP')).toEqual(['Italian GP']);
		expect(nameLines('United States Grand Prix')).toEqual(['United States', 'Grand Prix']);
		expect(nameLines('Emilia Romagna Grand Prix')).toEqual(['Emilia Romagna', 'Grand Prix']);
	});
});
