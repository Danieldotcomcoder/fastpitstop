import { describe, expect, it } from 'vitest';
import { battleLine, gapTrend } from './glance';
import type { BoardModel } from './pit-board';

const point = (lap: number, gapS: number | null) => ({ lap, gapS, lineS: null });

function model(a: [string, number], b: [string, number]): BoardModel {
	const driver = (code: string, compound: string, tyreAge: number) => ({
		driver: code,
		code,
		position: null,
		team: null,
		compound,
		tyreAge,
		inPit: false
	});
	return {
		state: 'waiting',
		attacker: driver('SAI', a[0], a[1]),
		defender: driver('PIA', b[0], b[1]),
		headline: { before: '', em: '', after: '' },
		sub: { before: '', em: '', after: '' },
		confidence: null,
		marginS: null,
		lowS: null,
		highS: null,
		intervalS: 0.44,
		boxAtLap: null,
		rejoin: null,
		pitLossS: null,
		pitLossNote: ''
	} as unknown as BoardModel;
}

describe('gapTrend', () => {
	it('reads the gap now against up to eight laps ago', () => {
		const spark = [20, 26, 27, 28, 29, 30, 31, 32, 33].map((lap, i) =>
			point(lap, lap === 20 ? 9 : 2.7 - i * 0.29)
		);
		const trend = gapTrend(spark)!;
		expect(trend.fromLap).toBe(26);
		expect(trend.fromS).toBeCloseTo(2.41, 2);
		expect(trend.direction).toBe('down');
	});

	it('skips laps with no measured gap and needs two', () => {
		expect(gapTrend([point(5, null), point(6, 1.2)])).toBeNull();
		expect(gapTrend([])).toBeNull();
	});

	it('calls a gap within a tenth steady', () => {
		expect(gapTrend([point(10, 1.2), point(12, 1.25)])!.direction).toBe('steady');
	});
});

describe('battleLine', () => {
	it('says how the gap moved and that the tyres match', () => {
		const line = battleLine(
			{ nowS: 0.44, fromS: 2.7, fromLap: 26, direction: 'down' },
			model(['hard', 6], ['hard', 6])
		);
		expect(line).toBe('Down from 2.7 s on lap 26. Same tyres, same age.');
	});

	it('leaves out the tyres when they differ', () => {
		const line = battleLine(
			{ nowS: 1, fromS: 0.5, fromLap: 30, direction: 'up' },
			model(['soft', 3], ['hard', 20])
		);
		expect(line).toBe('Up from 0.5 s on lap 30.');
	});
});
