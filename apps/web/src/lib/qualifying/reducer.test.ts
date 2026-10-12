import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { dropZoneSentences, needsToBeat, QualifyingReducer, qualiView } from './reducer';

const T0 = Date.parse('2024-05-04T20:25:00Z');
let tick = 0;
const env = (kind: string, payload: Record<string, unknown>, atS = tick++): Envelope => ({
	event_time_utc: new Date(T0 + atS * 1000).toISOString(),
	kind,
	payload
});
const iso = (s: number) => `PT${Math.floor(s / 60)}M${(s % 60).toFixed(3)}S`;

/** A 2024-style Q2: fifteen cars, ten go through, five Q1 knockouts. */
function q2(): QualifyingReducer {
	tick = 0;
	const r = new QualifyingReducer();
	const drivers = Array.from({ length: 20 }, (_, i) => ({
		number: String(i + 1),
		code: `D${String(i + 1).padStart(2, '0')}`,
		team: `Team ${Math.floor(i / 2)}`
	}));
	const events: Envelope[] = [
		env('driver_list_updated', { drivers }),
		env('qualifying_part_changed', { part: 1, entries: [20, 15, 10] }),
		...drivers.map((d, i) =>
			env('qualifying_best_lap', { driver: d.number, part: 1, lap_time: iso(88 + i / 10) })
		),
		env('qualifying_part_changed', { part: 2, entries: [20, 15, 10] }),
		...[16, 17, 18, 19, 20].map((n) =>
			env('qualifying_knockout', { driver: String(n), part: 1, knocked_out: true })
		),
		env('session_clock_updated', { remaining: 'PT15M', running: true }, 100),
		// Twelve of the fifteen have a Q2 time so far; car 3 is quickest.
		...Array.from({ length: 12 }, (_, i) =>
			env(
				'qualifying_best_lap',
				{ driver: String(i + 1), part: 2, lap_time: iso(87.5 + (i === 2 ? -0.4 : i / 10)) },
				200 + i
			)
		),
		env('pit_exit', { driver: '13' }, 300),
		env('pit_exit', { driver: '14' }, 300),
		env('lap_completed', { driver: '14', lap_number: 5 }, 400)
	];
	r.update(events);
	log = events;
	return r;
}

/** The log `q2()` built; a later release appends to it, as Delay Sync's does. */
let log: Envelope[] = [];
function release(r: QualifyingReducer, ...more: Envelope[]): void {
	log = [...log, ...more];
	r.update(log);
}

describe('the qualifying board', () => {
	it('ranks this part only, with the cut after the last car through', () => {
		const view = qualiView(q2().state, T0 + 500_000);
		expect(view.label).toBe('Q2');
		expect(view.advance).toBe(10);
		expect(view.rows).toHaveLength(15);
		expect(view.rows[0]!.code).toBe('D03');
		expect(view.rows.slice(0, 10).every((r) => !r.inDanger)).toBe(true);
		expect(view.rows.slice(10).every((r) => r.inDanger)).toBe(true);
		// The tenth-quickest Q2 time is the cut; cars without one are last.
		expect(view.cutTime).toBeCloseTo(88.4);
		expect(view.rows.slice(12).map((r) => [r.best, r.position])).toEqual([
			[null, null],
			[null, null],
			[null, null]
		]);
	});

	it('lists the knockouts in their final places', () => {
		const [q1] = qualiView(q2().state, T0 + 500_000).knockouts;
		expect(q1!.part).toBe(1);
		expect(q1!.rows.map((r) => [r.position, r.code])).toEqual([
			[16, 'D16'],
			[17, 'D17'],
			[18, 'D18'],
			[19, 'D19'],
			[20, 'D20']
		]);
	});

	it('counts the clock down on the viewer’s time, scaled to the replay speed', () => {
		const state = q2().state;
		// The clock read 15:00 at T0 + 100 s.
		expect(qualiView(state, T0 + 160_000).clockS).toBeCloseTo(840);
		expect(qualiView(state, T0 + 160_000, { timeScale: 4 }).clockS).toBeCloseTo(660);
		expect(qualiView(state, T0 + 2_000_000).clockState).toBe('over');
	});

	it('knows who is on an out lap and who is past it', () => {
		const rows = qualiView(q2().state, T0 + 500_000).rows;
		const by = (code: string) => rows.find((r) => r.code === code)!;
		expect(by('D13').status).toBe('out');
		expect(by('D14').status).toBe('track');
		expect(by('D01').status).toBeNull();
	});

	it('says what the drop zone needs, a thousandth under the cut', () => {
		const view = qualiView(q2().state, T0 + 500_000);
		const lap = (s: number) => s.toFixed(3);
		const [needs, margin] = dropZoneSentences(view, (r) => r.code, lap);
		expect(needs).toBe('D11, D12, D13, D14 and D15 need 88.399 or better to reach Q3.');
		expect(margin).toBe('D10 holds the last place in Q3 by 0.100 s over D11.');
		expect(needsToBeat(88.4)).toBe(88.399);
	});

	it('has no cut in the last part, and names sprint qualifying parts', () => {
		const r = q2();
		release(r, env('qualifying_part_changed', { part: 3, entries: [20, 15, 10] }, 900));
		const view = qualiView(r.state, T0 + 1_000_000, { sprint: true });
		expect(view.label).toBe('SQ3');
		expect(view.advance).toBeNull();
		expect(view.rows.every((row) => !row.inDanger)).toBe(true);
	});

	it('a car with no time in this part has no place yet', () => {
		const r = q2();
		release(
			r,
			env('qualifying_part_changed', { part: 3, entries: [20, 15, 10] }, 900),
			...[11, 12, 13, 14, 15].map((n) =>
				env('qualifying_knockout', { driver: String(n), part: 2, knocked_out: true }, 900)
			)
		);
		const view = qualiView(r.state, T0 + 1_000_000);
		// Ten cars in Q3, none timed: no places, in their Q2 order, no danger.
		expect(view.rows.map((row) => row.position)).toEqual(Array(10).fill(null));
		expect(view.rows[0]!.code).toBe('D03');
		expect(view.rows.every((row) => !row.inDanger)).toBe(true);
	});

	it('an untimed car is in danger only once every place through is taken', () => {
		const view = qualiView(q2().state, T0 + 500_000);
		// Twelve timed cars fill the ten places: the three untimed are out.
		expect(view.rows.filter((r) => r.position === null).every((r) => r.inDanger)).toBe(true);
	});

	it('a reinstated car is back in the order', () => {
		const r = q2();
		release(r, env('qualifying_knockout', { driver: '16', part: 1, knocked_out: false }, 950));
		expect(qualiView(r.state, T0 + 1_000_000).rows.some((row) => row.code === 'D16')).toBe(true);
	});
});
