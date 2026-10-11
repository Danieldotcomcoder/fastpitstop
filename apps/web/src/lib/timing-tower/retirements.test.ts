import { describe, expect, it } from 'vitest';
import { retiredDrivers } from './retirements';
import { createDriverRow, type DriverRow } from './types';

function car(driver: string, position: number, lapNumber: number | null): DriverRow {
	return { ...createDriverRow(driver), position, startPosition: position, lapNumber };
}

describe('retiredDrivers', () => {
	it('marks nobody before the start or while everyone is on the same lap', () => {
		expect(retiredDrivers([car('1', 1, null), car('4', 2, null)])).toEqual(new Set());
		expect(retiredDrivers([car('1', 1, 12), car('4', 2, 12), car('16', 3, 11)])).toEqual(new Set());
	});

	it('marks a car out once every car running behind it has gone two laps further', () => {
		// Miami 2024: Sargeant stopped after lap 27 in P18.
		const rows = [car('4', 1, 29), car('2', 18, 27), car('77', 17, 29), car('20', 19, 28)];
		expect(retiredDrivers(rows).has('2')).toBe(false); // Magnussen one lap on, not two
		rows[3] = car('20', 19, 29);
		expect(retiredDrivers(rows)).toEqual(new Set(['2']));
	});

	it('never marks out a car that everyone passed during a long stop', () => {
		// Miami 2024, lap 41: Piastri (old position P11, last lap 40) on his
		// out-lap after a front-wing change, with every car behind already
		// round to lap 41.
		const rows = [car('4', 1, 41), car('10', 11, 41), car('81', 11, 40)];
		for (let p = 12; p <= 18; p++) rows.push(car(String(p + 50), p, 41));
		expect(retiredDrivers(rows).has('81')).toBe(false);
	});

	it('ignores cars that stopped even earlier as witnesses', () => {
		const rows = [car('4', 1, 30), car('2', 10, 20), car('20', 11, 15), car('77', 12, 30)];
		expect(retiredDrivers(rows)).toEqual(new Set(['2', '20']));
	});

	it('counts a slower car behind as running, not the car ahead as last', () => {
		// São Paulo 2024, lap 32: Colapinto stopped after lap 30 in P16 with
		// Hülkenberg (P18) still running, a lap behind him.
		const rows = [
			car('31', 1, 32),
			car('24', 16, 31),
			car('43', 16, 30),
			// Running, two laps down after his spin.
			{ ...car('27', 18, 29), lapsDown: 2 }
		];
		expect(retiredDrivers(rows).has('43')).toBe(false);
	});

	it('is not blocked by another stopped car behind', () => {
		// São Paulo 2024: Colapinto stopped after lap 30 with Hülkenberg (also
		// stopped after 30) behind him; the running cars went on.
		const rows = [car('1', 1, 40), car('43', 14, 30), car('27', 15, 30), car('11', 16, 40)];
		expect(retiredDrivers(rows)).toEqual(new Set(['43', '27']));
	});

	it('resolves a pile-up from the back', () => {
		// Monaco 2024, lap 3: Pérez (P16), Hülkenberg (P19) and Magnussen
		// (P20) all stopped on lap 1, with only each other behind.
		const rows = [
			car('16', 1, 3),
			car('2', 15, 2),
			car('11', 16, 1),
			car('27', 19, 1),
			car('20', 20, 1)
		];
		expect(retiredDrivers(rows)).toEqual(new Set(['11', '27', '20']));
	});

	it('marks a lap-1 crash out once the cars behind its grid slot are two laps on', () => {
		const rows = [car('1', 1, 1), car('11', 2, null), car('16', 3, 1), car('55', 4, 1)];
		expect(retiredDrivers(rows).has('11')).toBe(false);
		const later = [car('1', 1, 2), car('11', 2, null), car('16', 3, 2), car('55', 4, 2)];
		expect(retiredDrivers(later)).toEqual(new Set(['11']));
	});

	it('marks the last car on the road out once the leader is two laps further', () => {
		const rows = [car('1', 1, 11), car('24', 20, 10)];
		expect(retiredDrivers(rows).has('24')).toBe(false);
		rows[0] = car('1', 1, 12);
		expect(retiredDrivers(rows)).toEqual(new Set(['24']));
	});

	it('allows the last car its own laps down', () => {
		// Monaco 2024: two laps down, the leader is three ahead each time it
		// crosses the line while the backmarker is still running.
		const backmarker = { ...car('2', 18, 66), lapsDown: 2 };
		expect(retiredDrivers([car('16', 1, 69), backmarker]).has('2')).toBe(false);
		expect(retiredDrivers([car('16', 1, 70), backmarker]).has('2')).toBe(true);
	});

	it('brings a car back when it completes another lap', () => {
		const rows = [car('1', 1, 22), car('44', 2, 20), car('16', 3, 22)];
		expect(retiredDrivers(rows).has('44')).toBe(true);
		rows[1] = car('44', 2, 21);
		expect(retiredDrivers(rows).has('44')).toBe(false);
	});

	it('does not mark lapped finishers out at the flag', () => {
		// Leader and the lead lap finish on 57; lapped cars on 56 and 55.
		const rows = [
			car('1', 1, 57),
			car('4', 2, 57),
			{ ...car('23', 15, 56), lapsDown: 1 },
			{ ...car('2', 16, 56), lapsDown: 1 },
			{ ...car('20', 17, 55), lapsDown: 2 }
		];
		expect(retiredDrivers(rows)).toEqual(new Set());
	});
});
