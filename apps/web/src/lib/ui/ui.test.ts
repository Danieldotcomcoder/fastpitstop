import { describe, expect, it } from 'vitest';
import { bannerFor } from './flags';
import { countdownParts, formatCountdown, formatDelta, positionDelta, sessionTime } from './format';
import { gapFraction, outlinePath, sparkSegments, stintSegments } from './geometry';

describe('sparkSegments', () => {
	it('maps larger values higher and spans the padded width', () => {
		const [line] = sparkSegments([0, 10], 100, 50, 2);
		expect(line).toBe('2,48 98,2');
	});

	it('breaks the line at unknown values instead of joining across them', () => {
		const segments = sparkSegments([1, 2, null, 3, 4], 100, 20, 0);
		expect(segments).toHaveLength(2);
		expect(segments[0]!.split(' ')).toHaveLength(2);
		expect(segments[1]!.split(' ')).toHaveLength(2);
	});

	it('draws a flat series through the middle and an empty one not at all', () => {
		expect(sparkSegments([5, 5, 5], 90, 30, 0)).toEqual(['0,15 45,15 90,15']);
		expect(sparkSegments([null, null], 90, 30)).toEqual([]);
	});

	it('honours a shared domain', () => {
		const [line] = sparkSegments([5], 100, 100, 0, [0, 10]);
		expect(line).toBe('50,50');
	});
});

describe('outlinePath', () => {
	it('flips y so a shape reported y-up is drawn y-up', () => {
		const path = outlinePath(
			[
				[0, 0],
				[10, 0],
				[10, 10]
			],
			100,
			100,
			0
		);
		// (10, 10) is the top-right corner on screen.
		expect(path).toBe('M0 100 L100 100 L100 0 Z');
	});

	it('keeps the aspect ratio and centres the shape', () => {
		const path = outlinePath(
			[
				[0, 0],
				[20, 0],
				[20, 10],
				[0, 10]
			],
			100,
			100,
			0
		);
		expect(path).toBe('M0 75 L100 75 L100 25 L0 25 Z');
	});

	it('returns nothing for fewer than two points', () => {
		expect(outlinePath([[1, 1]], 10, 10)).toBe('');
	});
});

describe('stintSegments', () => {
	const stints = [
		{ compound: 'MEDIUM', startLap: 1, endLap: 29 },
		{ compound: 'HARD', startLap: 30, endLap: null }
	];

	it('spans the race distance when it is known', () => {
		const segments = stintSegments(stints, 33, 57);
		expect(segments.map((s) => [s.fromLap, s.toLap])).toEqual([
			[1, 29],
			[30, 33]
		]);
		expect(segments[0]!.left).toBe(0);
		expect(segments[0]!.width).toBeCloseTo((29 / 57) * 100);
		expect(segments[1]!.left).toBeCloseTo((29 / 57) * 100);
		expect(segments[1]!.width).toBeCloseTo((4 / 57) * 100);
	});

	it('fills the bar with the laps run so far when the distance is unknown', () => {
		const segments = stintSegments(stints, 33, null);
		const end = segments.at(-1)!;
		expect(end.left + end.width).toBeCloseTo(100);
	});

	it('never draws past the lap the viewer has reached', () => {
		const future = [
			{ compound: 'MEDIUM', startLap: 1, endLap: 40 },
			{ compound: 'HARD', startLap: 41, endLap: null }
		];
		const segments = stintSegments(future, 33, 57);
		expect(segments).toHaveLength(1);
		expect(segments[0]!.toLap).toBe(33);
	});

	it('draws nothing before the start', () => {
		expect(stintSegments(stints, 0, 57)).toEqual([]);
	});
});

describe('gapFraction', () => {
	it('clamps to [0, 1] and treats unknown as empty', () => {
		expect(gapFraction(5, 10)).toBe(0.5);
		expect(gapFraction(30, 10)).toBe(1);
		expect(gapFraction(-1, 10)).toBe(0);
		expect(gapFraction(null, 10)).toBe(0);
		expect(gapFraction(3, 0)).toBe(0);
	});
});

describe('formatDelta', () => {
	it('signs values with a true minus and leaves zero unsigned', () => {
		expect(formatDelta(0.44)).toBe('+0.4');
		expect(formatDelta(-1.25, 2)).toBe('−1.25');
		expect(formatDelta(0.04)).toBe('0.0');
		expect(formatDelta(1, 1, ' s')).toBe('+1.0 s');
		expect(formatDelta(null)).toBe('—');
	});
});

describe('positionDelta', () => {
	it('counts a lower position as places gained', () => {
		expect(positionDelta(7, 4)).toEqual({ kind: 'gain', places: 3 });
		expect(positionDelta(1, 3)).toEqual({ kind: 'loss', places: 2 });
		expect(positionDelta(5, 5)).toEqual({ kind: 'same' });
		expect(positionDelta(null, 5)).toEqual({ kind: 'unknown' });
	});
});

describe('countdown', () => {
	const now = Date.UTC(2026, 9, 11, 10, 0, 0);

	it('splits the time left and formats days, then a clock', () => {
		const target = now + ((2 * 24 + 4) * 3600 + 12 * 60 + 9) * 1000;
		const parts = countdownParts(target, now);
		expect(parts).toEqual({ days: 2, hours: 4, minutes: 12, seconds: 9, past: false });
		expect(formatCountdown(parts)).toBe('2d 04h 12m');
		expect(formatCountdown(countdownParts(now + 3_725_000, now))).toBe('01:02:05');
	});

	it('reports a passed target as past, never negative', () => {
		expect(countdownParts(now - 5_000, now)).toEqual({
			days: 0,
			hours: 0,
			minutes: 0,
			seconds: 0,
			past: true
		});
	});
});

describe('sessionTime', () => {
	it('shows the start in the given time zone', () => {
		const iso = '2026-10-11T12:00:00Z';
		expect(sessionTime(iso, { timeZone: 'Asia/Singapore' })).toBe('Sun 20:00');
		expect(sessionTime(iso, { timeZone: 'Europe/London' })).toBe('Sun 13:00');
		expect(sessionTime('nonsense')).toBe('—');
	});
});

describe('bannerFor', () => {
	it('says nothing on green and names every other status', () => {
		expect(bannerFor('green')).toBeNull();
		expect(bannerFor('sc')).toEqual({ tone: 'sc', label: 'Safety car' });
		expect(bannerFor('vsc')?.label).toBe('Virtual safety car');
		expect(bannerFor('red')?.label).toBe('Red flag');
	});
});

describe('ToastQueue', () => {
	it('expires toasts after their time and keeps only the newest few', async () => {
		const { ToastQueue } = await import('./toasts');
		const queue = new ToastQueue(1_000, 2);
		queue.push('one', 0);
		queue.push('two', 100);
		queue.push('three', 200, 'Lap 4');
		expect(queue.visible(300).map((t) => t.text)).toEqual(['two', 'three']);
		expect(queue.visible(1_150).map((t) => t.text)).toEqual(['three']);
		expect(queue.visible(1_250)).toEqual([]);
	});

	it('dismisses one toast by id', async () => {
		const { ToastQueue } = await import('./toasts');
		const queue = new ToastQueue();
		const a = queue.push('a', 0);
		queue.push('b', 0);
		queue.dismiss(a.id);
		expect(queue.visible(1).map((t) => t.text)).toEqual(['b']);
	});
});
