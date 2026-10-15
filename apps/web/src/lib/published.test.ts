import { describe, expect, it } from 'vitest';
import fuel from '../../../../python/f1_kernel/reports/fuel_coefficient.md?raw';
import pitLoss from '../../../../python/f1_kernel/reports/pit_loss.md?raw';
import story from '../../../../python/f1_why/reports/story_eval.md?raw';
import { PUBLISHED } from './published';

describe('PUBLISHED', () => {
	it('matches the committed pit-loss report', () => {
		const head =
			/over (\d+) green-flag stop\(s\) across (\d+) session\(s\) \(seasons requested: (\d{4})/.exec(
				pitLoss
			);
		expect(head?.slice(1).map(Number)).toEqual([
			PUBLISHED.pitLoss.stops,
			PUBLISHED.pitLoss.races,
			PUBLISHED.pitLoss.season
		]);
		const calibrated = [...pitLoss.matchAll(/^\| (\w+) \| ([\d.]+) \|.*\| CALIBRATED \|$/gm)].map(
			(m) => ({ circuit: m[1], s: Number(m[2]) })
		);
		const low = calibrated.reduce((a, b) => (b.s < a.s ? b : a));
		const high = calibrated.reduce((a, b) => (b.s > a.s ? b : a));
		expect([low.circuit, low.s.toFixed(1)]).toEqual([
			'spa',
			PUBLISHED.pitLoss.lowest.seconds.toFixed(1)
		]);
		expect([high.circuit, high.s.toFixed(1)]).toEqual([
			'marina_bay',
			PUBLISHED.pitLoss.highest.seconds.toFixed(1)
		]);
	});

	it('matches the committed fuel report', () => {
		expect(Number(/`fuel_coef_s_per_kg` = \*\*([\d.]+)\*\*/.exec(fuel)?.[1])).toBe(
			PUBLISHED.fuel.sPerKg
		);
		const head = /over (\d+) stint\(s\) across (\d+) session\(s\)/.exec(fuel);
		expect(head?.slice(1).map(Number)).toEqual([PUBLISHED.fuel.stints, PUBLISHED.fuel.races]);
	});

	it('matches the committed story evaluation', () => {
		const row =
			/^\| (\d+) \| (\d+) \| (\d+)\/(\d+) \(\d+%\) \| (\d+)\/(\d+) \(\d+%\) \| 0 \|$/m.exec(story);
		expect(row?.slice(1).map(Number)).toEqual([
			PUBLISHED.story.races,
			PUBLISHED.story.cards,
			PUBLISHED.story.borneOut,
			PUBLISHED.story.cards,
			PUBLISHED.story.found,
			PUBLISHED.story.headlines
		]);
	});
});
