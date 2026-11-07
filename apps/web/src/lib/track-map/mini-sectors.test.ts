import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { MiniSectorTracker, profileAt } from './mini-sectors';

const T0 = Date.parse('2026-10-25T19:00:00.000Z');
const LAP_MS = 100_000;
/** A car exactly as quick everywhere: lap fraction equals time fraction. */
const EVEN = [0, 0.25, 0.5, 0.75, 1];
/** Enough cars that three laps make a quick-lap reference (30 lap times). */
const FIELD = ['1', '4', '10', '11', '14', '16', '18', '22', '23', '27', '44', '63'];
/** Four mini-sectors, ending at these fractions of a lap's time. */
const ENDS = [0.2, 0.5, 0.8, 1];

const env = (kind: string, payload: Record<string, unknown>, atMs: number): Envelope => ({
	event_time_utc: new Date(T0 + atMs).toISOString(),
	kind,
	payload
});

const mini = (driver: string, lap: number, index: number, atMs: number, status = 'normal') =>
	env(
		'mini_sector_completed',
		{ driver, lap_number: lap, mini_sector: index, mini_sector_count: 4, status },
		atMs
	);

/** `laps` clean laps for each driver, every mini-sector on time. */
function session(drivers: string[], laps: number): Envelope[] {
	const out: Envelope[] = [];
	for (const driver of drivers) out.push(env('lap_completed', { driver, lap_number: 0 }, 0));
	for (let lap = 1; lap <= laps; lap++) {
		const start = (lap - 1) * LAP_MS;
		for (const driver of drivers) {
			ENDS.forEach((tau, index) => {
				if (index < 3) out.push(mini(driver, lap, index, start + tau * LAP_MS));
			});
			out.push(mini(driver, lap, 3, start + LAP_MS - 1));
			out.push(env('lap_completed', { driver, lap_number: lap }, start + LAP_MS));
		}
	}
	return out.sort((a, b) => Date.parse(a.event_time_utc) - Date.parse(b.event_time_utc));
}

describe('profileAt', () => {
	it('reads the profile between its samples', () => {
		expect(profileAt([0, 0.2, 1], 0.25)).toBeCloseTo(0.1);
		expect(profileAt([0, 0.2, 1], 0.75)).toBeCloseTo(0.6);
		expect(profileAt([0, 0.2, 1], 2)).toBe(1);
	});
});

describe('MiniSectorTracker boundaries', () => {
	it('places each mini-sector where cars are when they complete it', () => {
		const t = new MiniSectorTracker();
		t.update(session(FIELD, 3));
		const ends = t.boundaries(EVEN)!;
		expect(ends.map((f) => Number(f.toFixed(3)))).toEqual([0.2, 0.5, 0.8, 1]);
	});

	it('reads time through the pace profile, so a slow first half moves them back', () => {
		const t = new MiniSectorTracker();
		t.update(session(FIELD, 3));
		// Half the lap's time covers only a third of its distance.
		const ends = t.boundaries([0, 1 / 6, 1 / 3, 2 / 3, 1])!;
		expect(ends[1]).toBeCloseTo(1 / 3, 3);
	});

	it('places nothing before every mini-sector has been seen on enough clean laps', () => {
		const t = new MiniSectorTracker();
		t.update(session(['1'], 1));
		expect(t.boundaries(EVEN)).toBeNull();
	});

	it('ignores the first laps after a restart, which include the wait', () => {
		const t = new MiniSectorTracker();
		const log: Envelope[] = FIELD.map((driver) =>
			env('lap_completed', { driver, lap_number: 0 }, 0)
		);
		// Lap 1 takes 1.4 laps' time: the first 40% stood still, then a lap.
		for (const driver of FIELD) {
			ENDS.forEach((tau, index) =>
				log.push(mini(driver, 1, index, (0.4 + tau) * LAP_MS - (index === 3 ? 1 : 0)))
			);
			log.push(env('lap_completed', { driver, lap_number: 1 }, 1.4 * LAP_MS));
		}
		for (const e of session(FIELD, 3)) {
			const lap = e.payload.lap_number;
			if (typeof lap !== 'number' || lap < 1) continue;
			const shifted = Date.parse(e.event_time_utc) - T0 + 0.4 * LAP_MS;
			log.push({
				...e,
				payload: { ...e.payload, lap_number: lap + 1 },
				event_time_utc: new Date(T0 + shifted).toISOString()
			});
		}
		log.sort((a, b) => Date.parse(a.event_time_utc) - Date.parse(b.event_time_utc));
		t.update(log);
		expect(t.boundaries(EVEN)![0]).toBeCloseTo(0.2, 3);
	});

	it('leaves pit-lane passes and slow laps out of the placing', () => {
		const t = new MiniSectorTracker();
		const events = session(FIELD, 3);
		// Car 16's laps run at half speed through the pits: ignored.
		events.push(env('lap_completed', { driver: '16', lap_number: 0 }, 0));
		events.push(mini('16', 1, 1, 0.9 * LAP_MS, 'pit_lane'));
		events.push(mini('16', 1, 0, 0.9 * LAP_MS));
		events.push(env('lap_completed', { driver: '16', lap_number: 1 }, 2 * LAP_MS));
		events.sort((a, b) => Date.parse(a.event_time_utc) - Date.parse(b.event_time_utc));
		t.update(events);
		expect(t.boundaries(EVEN)![0]).toBeCloseTo(0.2, 3);
		expect(t.boundaries(EVEN)![1]).toBeCloseTo(0.5, 3);
	});

	it('starts again when the lap layout changes', () => {
		const t = new MiniSectorTracker();
		const log = session(FIELD, 3);
		t.update(log);
		log.push(
			env(
				'mini_sector_completed',
				{ driver: '1', lap_number: 4, mini_sector: 0, mini_sector_count: 5, status: 'normal' },
				3 * LAP_MS + 5_000
			)
		);
		t.update(log);
		expect(t.miniSectorCount).toBe(5);
		expect(t.boundaries(EVEN)).toBeNull();
	});
});

describe('MiniSectorTracker placing a car', () => {
	const tracker = (): MiniSectorTracker => {
		const t = new MiniSectorTracker();
		const log = session(FIELD, 3);
		t.update(log);
		// Lap 4 under way: car 1 completed mini-sector 1 (ends at 0.5) at 350 s.
		log.push(mini('1', 4, 0, 3 * LAP_MS + 20_000), mini('1', 4, 1, 3 * LAP_MS + 50_000));
		t.update(log);
		return t;
	};

	it('moves the car on from its latest timing point at its own pace', () => {
		const t = tracker();
		// 15 s later: half of the 30 s to the next timing point.
		expect(t.fractionAt('1', T0 + 3 * LAP_MS + 65_000, EVEN)).toBeCloseTo(0.65, 3);
	});

	it('measures in race time when a replay stamps the stream faster', () => {
		// The same race replayed at 16x: stamps 16 times closer together, each
		// payload keeping the race's own time.
		const t = new MiniSectorTracker();
		const log = session(FIELD, 3);
		log.push(mini('1', 4, 0, 3 * LAP_MS + 20_000), mini('1', 4, 1, 3 * LAP_MS + 50_000));
		const replayed = log.map((e) => {
			const race = Date.parse(e.event_time_utc);
			return {
				...e,
				event_time_utc: new Date(T0 + (race - T0) / 16).toISOString(),
				payload: { ...e.payload, ts_utc: e.event_time_utc }
			};
		});
		t.update(replayed);
		// 15 s of race after the timing point is 0.9375 s of stream at 16x.
		const at = T0 + (3 * LAP_MS + 50_000) / 16 + 937.5;
		expect(t.fractionAt('1', at, EVEN, 16)).toBeCloseTo(0.65, 3);
	});

	it('runs at the replay speed', () => {
		const t = tracker();
		// At 10x, 1.5 s of stream time is 15 s of race.
		expect(t.fractionAt('1', T0 + 3 * LAP_MS + 51_500, EVEN, 10)).toBeCloseTo(0.65, 3);
	});

	it('holds a late car just short of its next timing point instead of running past it', () => {
		const t = tracker();
		const late = t.fractionAt('1', T0 + 3 * LAP_MS + 100_000, EVEN)!;
		expect(late).toBeLessThan(0.8);
		expect(late).toBeGreaterThan(0.79);
	});

	it('stops placing a car whose timing has gone quiet (stopped, in the garage)', () => {
		const t = tracker();
		expect(t.fractionAt('1', T0 + 3 * LAP_MS + 200_000, EVEN)).toBeNull();
	});

	it('places nobody without a pace profile', () => {
		expect(tracker().fractionAt('1', T0 + 3 * LAP_MS + 65_000, null)).toBeNull();
	});

	it('keeps placing a car that has just crossed the line', () => {
		const t = new MiniSectorTracker();
		t.update(session(FIELD, 3));
		// 10 s after the line, half way to the first timing point (0.2).
		expect(t.fractionAt('1', T0 + 3 * LAP_MS + 10_000, EVEN)).toBeCloseTo(0.1, 2);
	});

	it('follows the pace of its last few mini-sectors, as behind a safety car', () => {
		const t = new MiniSectorTracker();
		const log = session(FIELD, 3);
		t.update(log);
		// Lap 4 at half pace: three timing points, each twice as long apart.
		log.push(
			mini('1', 4, 0, 3 * LAP_MS + 40_000),
			mini('1', 4, 1, 3 * LAP_MS + 100_000),
			mini('1', 4, 2, 3 * LAP_MS + 160_000)
		);
		t.update(log);
		// 30 s on at half pace is 0.15 of a lap past 0.8.
		expect(t.fractionAt('1', T0 + 3 * LAP_MS + 190_000, EVEN)).toBeCloseTo(0.95, 2);
	});

	it('measures the boundaries from quick laps when half the laps are out-laps', () => {
		const t = new MiniSectorTracker();
		const log = session(FIELD, 3);
		// Car 77 runs only slow laps (twice the time, evenly): left out.
		log.push(env('lap_completed', { driver: '77', lap_number: 0 }, 0));
		for (let lap = 1; lap <= 2; lap++) {
			const start = (lap - 1) * 2 * LAP_MS;
			[0.35, 0.6, 0.9].forEach((tau, index) =>
				log.push(mini('77', lap, index, start + tau * 2 * LAP_MS))
			);
			log.push(env('lap_completed', { driver: '77', lap_number: lap }, start + 2 * LAP_MS));
		}
		log.sort((a, b) => Date.parse(a.event_time_utc) - Date.parse(b.event_time_utc));
		t.update(log);
		expect(t.boundaries(EVEN)![0]).toBeCloseTo(0.2, 3);
	});
});

describe('MiniSectorTracker lap colours', () => {
	it('keeps the latest lap only, so a new lap starts uncoloured', () => {
		const t = new MiniSectorTracker();
		t.update([
			mini('1', 4, 0, 1_000, 'personal_best'),
			mini('1', 4, 1, 2_000, 'overall_best'),
			mini('1', 5, 0, 3_000, 'normal')
		]);
		expect([...t.lapStatuses('1')]).toEqual([[0, 'normal']]);
	});
});
