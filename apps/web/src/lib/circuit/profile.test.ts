import { describe, expect, it } from 'vitest';
import { pitLossView, wearRows, wearSeasons, type PitLossInput } from './profile';

const circuit = (
	circuit_id: string,
	pit_loss_s: number,
	calibrated = true,
	n = 20,
	unc = 0.4
): PitLossInput => ({
	circuit_id,
	pit_loss_s,
	pit_loss_provenance: calibrated ? 'calibrated' : 'provisional',
	pit_loss_uncertainty_s: calibrated ? unc : null,
	pit_loss_n_stops: calibrated ? n : null
});

const all = [
	circuit('marina_bay', 30.1, true, 18, 0.5),
	circuit('imola', 28.5),
	circuit('silverstone', 26.4, true, 33, 1.75),
	circuit('bahrain', 24.3),
	circuit('spa', 18.8),
	circuit('monaco', 19.0, false)
];

describe('pitLossView', () => {
	it('ranks a measured circuit among the measured ones only', () => {
		const view = pitLossView(all[0]!, all);
		expect(view.value).toBe('30.1 s');
		expect(view.detail).toBe('± 0.5 s · measured over 18 stops');
		expect(view.rank).toBe('highest of 5 measured circuits');
		expect(pitLossView(all[4]!, all).rank).toBe('lowest of 5 measured circuits');
		expect(pitLossView(all[1]!, all).rank).toBe('2nd highest of 5 measured circuits');
	});

	it('never dresses a default up as a measurement', () => {
		const view = pitLossView(all[5]!, all);
		expect(view.measured).toBe(false);
		expect(view.rank).toBeNull();
		expect(view.tier).toBe('unreliable');
		expect(view.detail).toMatch(/not measured/i);
	});

	it('calls a wide or small measurement thin', () => {
		expect(pitLossView(all[2]!, all).tier).toBe('thin');
		expect(pitLossView(circuit('x', 22, true, 8, 0.3), all).tier).toBe('thin');
		expect(pitLossView(all[3]!, all).tier).toBe('good');
	});
});

describe('wearRows', () => {
	const wear = [
		{ compound: 'hard', median_s_per_lap: 0.06, good_fits: 30, sessions: ['2024-18', '2025-19'] },
		{ compound: 'soft', median_s_per_lap: 0.12, good_fits: 1, sessions: ['2024-18'] },
		{ compound: 'medium', median_s_per_lap: 0.08, good_fits: 5, sessions: ['2025-19'] }
	];

	it('orders softest first and labels the evidence behind each', () => {
		const rows = wearRows(wear);
		expect(rows.map((r) => r.compound)).toEqual(['soft', 'medium', 'hard']);
		expect(rows.map((r) => r.tier)).toEqual(['unreliable', 'thin', 'good']);
		expect(rows[0]!.label).toBe('+0.120 s/lap');
		expect(rows[0]!.fraction).toBe(1);
		expect(rows[2]!.fraction).toBeCloseTo(0.5);
	});

	it('draws no bar for tyres that got faster with age', () => {
		const [row] = wearRows([
			{ compound: 'medium', median_s_per_lap: -0.02, good_fits: 9, sessions: [] }
		]);
		expect(row!.fraction).toBe(0);
		expect(row!.label).toBe('−0.020 s/lap');
	});

	it('names the seasons the figures came from', () => {
		expect(wearSeasons(wear)).toBe('2024 · 2025');
	});
});
