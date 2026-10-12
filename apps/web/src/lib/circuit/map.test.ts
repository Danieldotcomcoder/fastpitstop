import { describe, expect, it } from 'vitest';
import { circuitIdFromSlug, circuitMap, circuitSlug } from './map';

// A 1 km square lap, y-up as the cars report it, starting bottom-left and
// running anticlockwise: 250 m per side.
const square: [number, number][] = [
	[0, 0],
	[100, 0],
	[100, 100],
	[0, 100]
];

describe('circuitMap', () => {
	const map = circuitMap(
		square,
		1000,
		[
			{ number: 1, distance_m: 250 },
			{ number: 2, letter: 'a', distance_m: 500 }
		],
		[{ start_m: 0, end_m: 250 }],
		120,
		120,
		10
	)!;

	it('starts at the line and flips y so the shape is drawn y-up', () => {
		// Anticlockwise from bottom-left: heading right along the bottom.
		expect(map.start).toEqual({ x: 10, y: 110, angle: 0 });
		expect(map.path.startsWith('M10 110 L110 110')).toBe(true);
	});

	it('places each corner by its distance round the lap', () => {
		expect(map.corners).toEqual([
			{ label: '1', x: 110, y: 110 },
			{ label: '2a', x: 110, y: 10 }
		]);
	});

	it('draws a DRS zone along the track between its distances', () => {
		const [zone] = map.drs;
		expect(zone!.startsWith('M10 110')).toBe(true);
		expect(zone!.endsWith('L110 110')).toBe(true);
	});

	it('carries a zone that crosses the line on round it', () => {
		const wrapped = circuitMap(square, 1000, [], [{ start_m: 750, end_m: 250 }], 120, 120, 10)!;
		const [zone] = wrapped.drs;
		expect(zone!.startsWith('M10 10')).toBe(true);
		expect(zone).toContain('L10 110');
		expect(zone!.endsWith('L110 110')).toBe(true);
	});

	it('has nothing to draw without an outline', () => {
		expect(circuitMap([], 1000, [], [], 100, 100)).toBeNull();
	});
});

describe('slugs', () => {
	it('hyphenate the file id for the URL, and back', () => {
		expect(circuitSlug('marina_bay')).toBe('marina-bay');
		expect(circuitIdFromSlug('red-bull-ring')).toBe('red_bull_ring');
	});
});
