import { describe, expect, it } from 'vitest';
import { callText, groupOf, latestFirst } from './text';
import type { CallOut } from './view';

const NAMES: Record<string, string> = {
	'16': 'Leclerc',
	'11': 'Pérez',
	'4': 'Norris',
	'63': 'Russell',
	'81': 'Piastri'
};
const name = (d: string) => NAMES[d] ?? d;

function call(over: Partial<CallOut>): CallOut {
	return {
		call_id: 'x',
		kind: 'undercut',
		driver: '16',
		rival: '11',
		decision_lap: 12,
		in_lap: 13,
		confidence: 'high',
		status: 'pending',
		detail: null,
		window: 'closed',
		crossover_lap: null,
		projected_position: null,
		projected_margin_s: null,
		rival_stop_lap: null,
		driver_ahead_after: null,
		actual_position: null,
		actual_car_ahead: null,
		...over
	};
}

describe('undercut calls', () => {
	it('says what was called and how it settled', () => {
		const t = callText(
			call({
				status: 'right',
				crossover_lap: 16,
				rival_stop_lap: 15,
				driver_ahead_after: false
			}),
			name
		);
		expect(t.lap).toBe('L13');
		expect(t.kind).toBe('Undercut');
		expect(t.body).toBe(
			'Leclerc boxed to undercut Pérez. We said it comes through if Pérez stays out to lap 16. Pérez stopped on lap 15. Leclerc came out behind.'
		);
		expect(t.outcome).toBe('Right');
		expect(t.note).toBeNull();
	});
	it('says what a waiting call waits for', () => {
		const t = callText(call({ detail: 'waiting_for_rival_stop', crossover_lap: 17 }), name);
		expect(t.body).toBe(
			'Leclerc boxed to undercut Pérez. We said it comes through if Pérez stays out to lap 17.'
		);
		expect(t.note).toBe('Settles when Pérez stops.');
		expect(t.outcome).toBe('Waiting');
	});
	it('gives the reason a call is not counted', () => {
		const t = callText(call({ status: 'void', detail: 'rival_stopped_first' }), name);
		expect(t.body).toBe('Leclerc boxed to undercut Pérez.');
		expect(t.note).toBe('Pérez had stopped the lap before: it was never an undercut on Pérez.');
		expect(t.outcome).toBe('Not counted');
	});
	it('says so when there was too little to call', () => {
		const t = callText(call({ status: 'no_call', window: 'unknown' }), name);
		expect(t.note).toBe('Not enough clean laps to call it.');
		expect(t.outcome).toBe('No call');
	});
});

describe('rejoin calls', () => {
	const rejoin = (over: Partial<CallOut>) =>
		call({
			kind: 'rejoin',
			driver: '4',
			rival: '81',
			window: null,
			projected_position: 8,
			projected_margin_s: 4.2,
			...over
		});
	it('says where it was called to come out, and where it did', () => {
		const t = callText(
			rejoin({ status: 'wrong', actual_position: 9, actual_car_ahead: '63' }),
			name
		);
		expect(t.kind).toBe('Rejoin');
		expect(t.body).toBe(
			'Norris boxed. We said P8, behind Piastri, 4.2 s from the nearest car. Norris came out P9, behind Russell.'
		);
		expect(t.outcome).toBe('Wrong');
	});
	it('gives the reason a call is not counted', () => {
		const t = callText(
			rejoin({ status: 'void', detail: 'another_car_pitted_around_the_rejoin' }),
			name
		);
		expect(t.body).toBe('Norris: P8, behind Piastri.');
		expect(t.note).toContain('pitted on the same laps');
	});
});

describe('grouping and order', () => {
	it('puts each status in its ledger group', () => {
		expect(groupOf(call({ status: 'right' }))).toBe('counted');
		expect(groupOf(call({ status: 'wrong' }))).toBe('counted');
		expect(groupOf(call({ status: 'pending' }))).toBe('waiting');
		expect(groupOf(call({ status: 'void' }))).toBe('not_counted');
		expect(groupOf(call({ status: 'no_call' }))).toBe('not_counted');
	});
	it('lists the newest first, keeping lock order within a lap', () => {
		const a = call({ call_id: 'a', in_lap: 12 });
		const b = call({ call_id: 'b', in_lap: 16 });
		const c = call({ call_id: 'c', in_lap: 12 });
		expect(latestFirst([a, b, c]).map((x) => x.call_id)).toEqual(['b', 'c', 'a']);
	});
});
