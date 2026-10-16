import { describe, expect, it } from 'vitest';
import {
	callKindsFromSnapshot,
	rejoinsFromSnapshot,
	stripModel,
	type RejoinProjection
} from './rejoin';

const NAMES: Record<string, string> = { '16': 'Leclerc', '63': 'Russell', '4': 'Norris' };
const CODES: Record<string, string> = { '16': 'LEC', '63': 'RUS', '4': 'NOR' };
const name = (d: string) => NAMES[d] ?? d;
const code = (d: string) => CODES[d] ?? d;
const team = () => null;

function projection(over: Partial<RejoinProjection> = {}): RejoinProjection {
	return {
		driver: '16',
		decision_lap: 16,
		in_lap: 17,
		position_now: 4,
		position: 7,
		car_ahead: '63',
		gap_to_ahead_s: 3.6,
		car_behind: '4',
		gap_to_behind_s: 1.1,
		margin_s: 1.1,
		pit_loss_s: 24.3,
		pit_loss_provenance: 'measured',
		pit_loss_sigma_s: 0.9,
		confidence: 'medium',
		reasons: [
			'lapped cars and pace over the in-lap and out-lap are not modelled',
			'1.1 s from the nearest car, within 2 x the 0.9 s spread of one stop'
		],
		...over
	};
}

describe('reading the snapshot', () => {
	it('keeps only well-formed projections', () => {
		const good = projection();
		expect(rejoinsFromSnapshot({ rejoins: [good, { driver: 1 }, null] })).toEqual([good]);
		expect(rejoinsFromSnapshot(null)).toEqual([]);
		expect(rejoinsFromSnapshot({})).toEqual([]);
	});
	it('reads the served call kinds, nothing unknown', () => {
		expect(callKindsFromSnapshot({ call_kinds: ['rejoin', 'pit_window', 3] })).toEqual(['rejoin']);
		expect(callKindsFromSnapshot({})).toEqual([]);
	});
});

describe('stripModel', () => {
	it('says where the car comes out, from the kernel only', () => {
		const m = stripModel(projection(), name, code, team);
		expect(m.kicker).toBe('If Leclerc boxes at the end of this lap');
		expect(m.headline).toBe('P7, 3.6 s behind Russell');
		expect(m.sub).toBe('1.1 s ahead of Norris. Down three places from P4.');
		expect(m.confidence).toBe('medium');
		expect(m.reason).toBe('1.1 s from the nearest car, within 2 x the 0.9 s spread of one stop.');
		expect(m.caveat).toBe('Lapped cars and pace over the in-lap and out-lap are not modelled.');
		expect(m.out.atS).toBe(24.3);
		expect(m.ahead?.atS).toBeCloseTo(20.7);
		expect(m.behind?.atS).toBeCloseTo(25.4);
		expect(m.moreAhead).toBe(5);
	});
	it('puts every drawn mark and both bands inside the axis', () => {
		const m = stripModel(projection(), name, code, team);
		for (const x of [m.out.atS, m.ahead?.atS ?? 0, m.behind?.atS ?? 0, 24.3 - 1.8, 24.3 + 1.8]) {
			expect(x).toBeGreaterThan(m.domain.lo);
			expect(x).toBeLessThan(m.domain.hi);
		}
		expect(m.ticks[0]).toBe(m.domain.lo);
		expect(m.ticks.every((t) => t % 5 === 0)).toBe(true);
	});
	it('reads a car that would lead, and one that keeps its place', () => {
		const lead = stripModel(
			projection({ position: 1, position_now: 1, car_ahead: null, gap_to_ahead_s: null }),
			name,
			code,
			team
		);
		expect(lead.headline).toBe('P1, in the lead');
		expect(lead.sub).toBe('1.1 s ahead of Norris. Keeps P1.');
		expect(lead.ahead).toBeNull();
		expect(lead.moreAhead).toBe(0);
	});
	it('says so when no spread is measured', () => {
		const m = stripModel(projection({ pit_loss_sigma_s: null, reasons: [] }), name, code, team);
		expect(m.sigmaS).toBeNull();
		expect(m.reason).toBeNull();
		expect(m.caveat).toBeNull();
		expect(m.label).not.toContain('bands');
	});
});
