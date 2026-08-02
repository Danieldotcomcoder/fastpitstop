import { describe, expect, it } from 'vitest';
import { buildGapStreamFrame } from './gap-stream-frame';
import { createGapStreamState } from './gap-stream';

describe('buildGapStreamFrame', () => {
	it('drops samples with an unknown lap number or a lapped/unknown gap', () => {
		const state = createGapStreamState();
		state.drivers.set('44', {
			driver: '44',
			code: 'HAM',
			team: 'Mercedes',
			samples: [
				{ lapNumber: null, tsMs: 0, gapToLeaderS: 1, lapsDown: null },
				{ lapNumber: 1, tsMs: 1000, gapToLeaderS: null, lapsDown: 1 },
				{ lapNumber: 1, tsMs: 2000, gapToLeaderS: 2.5, lapsDown: null }
			]
		});

		const frame = buildGapStreamFrame(state);
		expect(frame.bands).toHaveLength(1);
		expect(frame.bands[0]?.points).toEqual([{ lap: 1, gapS: 2.5, tsMs: 2000 }]);
		expect(frame.maxLap).toBe(1);
		expect(frame.maxGapS).toBeCloseTo(2.5, 6);
	});

	it('omits a driver with no plottable points entirely', () => {
		const state = createGapStreamState();
		state.drivers.set('1', { driver: '1', code: 'VER', team: 'Red Bull Racing', samples: [] });
		const frame = buildGapStreamFrame(state);
		expect(frame.bands).toHaveLength(0);
	});

	it('resolves a team color per band', () => {
		const state = createGapStreamState();
		state.drivers.set('1', {
			driver: '1',
			code: 'VER',
			team: 'Red Bull Racing',
			samples: [{ lapNumber: 1, tsMs: 0, gapToLeaderS: 0, lapsDown: null }]
		});
		const frame = buildGapStreamFrame(state);
		expect(frame.bands[0]?.color).toMatch(/^#/);
	});
});
