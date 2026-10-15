import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { createDriverRow, type DriverRow } from '$lib/timing-tower';
import { battleTrend, battleVerdict, fieldLine, ribbonModel, teammateDashed } from './hero';
import type { BoardDriver } from './pit-board';
import { MomentToasts } from './moments';
import { RaceHistoryReducer } from './race-history';

let seq = 0;
function envelope(kind: string, payload: Record<string, unknown>): Envelope {
	return { event_time_utc: new Date(seq++ * 1000).toISOString(), kind, payload };
}
const lap = (driver: string, n: number) => envelope('lap_completed', { driver, lap_number: n });
const pos = (driver: string, p: number) => envelope('position_changed', { driver, position: p });
const gap = (driver: string, s: number | null, lapsDown: number | null = null) =>
	envelope('gap_updated', {
		driver,
		gap_to_leader: s === null ? null : `PT${s}S`,
		laps_down: lapsDown
	});
const rc = (category: string, message: string, flag: string | null = null, scope = 'Track') =>
	envelope('race_control_message', { category, message, flag, scope });

function row(driver: string, overrides: Partial<DriverRow> = {}): DriverRow {
	return { ...createDriverRow(driver), code: `D${driver}`, ...overrides };
}
function board(driver: string, compound: string, tyreAge: number): BoardDriver {
	return {
		driver,
		code: `D${driver}`,
		position: null,
		team: null,
		compound,
		tyreAge,
		inPit: false
	};
}

/** A small seeded PRNG, so the property test is reproducible. */
function rng(seed: number): () => number {
	let s = seed >>> 0;
	return () => {
		s = (s * 1664525 + 1013904223) >>> 0;
		return s / 2 ** 32;
	};
}

describe('RaceHistoryReducer', () => {
	it('records each car at the end of every lap, starting from the grid', () => {
		const h = new RaceHistoryReducer();
		h.update([pos('1', 1), pos('4', 2), lap('1', 1), lap('4', 1), pos('4', 1), pos('1', 2)]);
		expect(h.points('4')).toEqual([
			{ lap: 0, position: 2, gapS: null },
			{ lap: 1, position: 1, gapS: null }
		]);
		expect(h.points('1').map((p) => p.position)).toEqual([1, 2]);
		expect(h.leaderLap).toBe(1);
	});

	it('shades safety-car laps from race control when the feed sends no track status', () => {
		const h = new RaceHistoryReducer();
		h.update([
			lap('1', 1),
			rc('SafetyCar', 'SAFETY CAR DEPLOYED'),
			lap('1', 2),
			lap('1', 3),
			rc('SafetyCar', 'SAFETY CAR IN THIS LAP'),
			lap('1', 4),
			rc('Flag', 'TRACK CLEAR', 'CLEAR'),
			lap('1', 5)
		]);
		expect(h.bands()).toEqual([{ fromLap: 2, toLap: 4, status: 'sc' }]);
	});

	it('upgrades a VSC to a safety car and ignores sector and driver flags', () => {
		const h = new RaceHistoryReducer();
		h.update([
			rc('SafetyCar', 'VIRTUAL SAFETY CAR DEPLOYED'),
			lap('1', 1),
			rc('Flag', 'YELLOW IN SECTOR 2', 'YELLOW', 'Sector'),
			rc('Flag', 'BLUE FLAG FOR CAR 2', 'BLUE', 'Driver'),
			rc('SafetyCar', 'SAFETY CAR DEPLOYED'),
			lap('1', 2),
			rc('Flag', 'GREEN LIGHT', 'GREEN'),
			lap('1', 3)
		]);
		expect(h.bands()).toEqual([
			{ fromLap: 1, toLap: 1, status: 'vsc' },
			{ fromLap: 2, toLap: 2, status: 'sc' }
		]);
	});

	it("prefers the feed's own track status over race control", () => {
		const h = new RaceHistoryReducer();
		h.update([
			envelope('track_status_changed', { status: 'vsc' }),
			rc('SafetyCar', 'SAFETY CAR DEPLOYED'),
			lap('1', 1),
			envelope('track_status_changed', { status: 'green' }),
			lap('1', 2),
			lap('1', 3)
		]);
		// The VSC ended during lap 2, so lap 2 ran partly under it.
		expect(h.bands()).toEqual([{ fromLap: 1, toLap: 2, status: 'vsc' }]);
	});

	it('gives the gap between two cars, lap by lap, and drops lapped laps', () => {
		const h = new RaceHistoryReducer();
		h.update([
			lap('1', 1),
			lap('4', 1),
			lap('16', 1),
			gap('4', 1.0),
			gap('16', 3.5),
			lap('1', 2),
			lap('4', 2),
			lap('16', 2),
			gap('4', 1.2),
			gap('16', null, 1)
		]);
		expect(h.pairGaps('16', '4')).toEqual([{ lap: 1, gapS: 2.5 }]);
	});

	it("counts the leader's gap as zero, since the feed never sends it", () => {
		const h = new RaceHistoryReducer();
		h.update([pos('1', 1), pos('11', 2), lap('1', 1), lap('11', 1), gap('11', 2.98)]);
		expect(h.pairGaps('11', '1')).toEqual([{ lap: 1, gapS: 2.98 }]);
	});

	it("keeps each lap's gap as sent at the line, not the sector updates after it", () => {
		const h = new RaceHistoryReducer();
		// The released log only ever grows; the reducer reads it from its cursor.
		const log: Envelope[] = [lap('1', 1), lap('11', 1), gap('11', 2.9), gap('11', 3.1)];
		h.update(log);
		expect(h.points('11').at(-1)?.gapS).toBe(2.9);
		// Sent in the same instant as the crossing, before the lap event.
		const at = new Date(Date.UTC(2030, 0, 1)).toISOString();
		log.push(
			{
				event_time_utc: at,
				kind: 'gap_updated',
				payload: { driver: '11', gap_to_leader: 'PT2.5S' }
			},
			{ event_time_utc: at, kind: 'lap_completed', payload: { driver: '11', lap_number: 2 } }
		);
		h.update(log);
		expect(h.points('11').at(-1)).toMatchObject({ lap: 2, gapS: 2.5 });
		log.push(gap('11', 2.7));
		h.update(log);
		expect(h.points('11').at(-1)).toMatchObject({ lap: 2, gapS: 2.5 });
	});

	it('marks the lap a car pitted on', () => {
		const h = new RaceHistoryReducer();
		h.update([lap('4', 1), lap('4', 2), envelope('pit_entry', { driver: '4' })]);
		expect(h.pitLaps('4')).toEqual([3]);
	});

	it('property: never holds a lap the released log has not completed', () => {
		const random = rng(7);
		for (let trial = 0; trial < 100; trial++) {
			const drivers = ['1', '4', '11', '16', '55'];
			const events: Envelope[] = [];
			const laps = 3 + Math.floor(random() * 12);
			for (let n = 1; n <= laps; n++) {
				if (random() < 0.15) events.push(rc('SafetyCar', 'SAFETY CAR DEPLOYED'));
				if (random() < 0.2) events.push(rc('Flag', 'TRACK CLEAR', 'CLEAR'));
				for (const d of [...drivers].sort(() => random() - 0.5)) {
					events.push(lap(d, n));
					events.push(pos(d, 1 + Math.floor(random() * drivers.length)));
					events.push(gap(d, Math.round(random() * 300) / 10));
				}
			}
			const cut = Math.floor(random() * events.length);
			const released = events.slice(0, cut);
			const done = new Map<string, number>();
			for (const e of released) {
				if (e.kind === 'lap_completed') {
					const d = e.payload.driver as string;
					done.set(d, Math.max(done.get(d) ?? 0, e.payload.lap_number as number));
				}
			}
			const h = new RaceHistoryReducer();
			h.update(released);
			const leader = Math.max(0, ...done.values());
			expect(h.leaderLap).toBe(leader);
			for (const d of drivers) {
				for (const p of h.points(d)) expect(p.lap).toBeLessThanOrEqual(done.get(d) ?? 0);
			}
			for (const b of h.bands()) expect(b.toLap).toBeLessThanOrEqual(leader + 1);
			const rows = drivers.map((d, i) => row(d, { position: i + 1 }));
			const model = ribbonModel(h, rows, {}, null);
			for (const line of model.lines) {
				for (const p of line.points) expect(p.lap).toBeLessThanOrEqual(model.reached);
			}
			expect(model.axisEnd).toBe(Math.max(leader, 1));
		}
	});
});

describe('battle card', () => {
	function history(): RaceHistoryReducer {
		const h = new RaceHistoryReducer();
		const events: Envelope[] = [];
		const behind = [2.7, 2.6, 2.6, 0.9, 0.8, 0.7, 0.5, 0.44];
		behind.forEach((g, i) => {
			const n = 26 + i;
			if (n === 29) events.push(rc('SafetyCar', 'SAFETY CAR DEPLOYED'));
			if (n === 30) events.push(rc('Flag', 'TRACK CLEAR', 'CLEAR'));
			events.push(lap('1', n), lap('81', n), lap('55', n), gap('81', 5), gap('55', 5 + g));
		});
		h.update(events);
		return h;
	}

	it('reads the trend and how much of it came behind the safety car', () => {
		const trend = battleTrend(history(), '55', '81');
		expect(trend).not.toBeNull();
		expect(trend?.fromLap).toBe(26);
		expect(trend?.nowS).toBeCloseTo(0.44);
		expect(trend?.direction).toBe('down');
		expect(trend?.neutral).toBe('sc');
		const text = battleVerdict(
			trend,
			board('55', 'hard', 6),
			board('81', 'hard', 6),
			'Sainz',
			'Piastri'
		);
		expect(text).toBe(
			'Down from 2.7 s on lap 26, most of it behind the safety car (shaded). Same tyres, same age.'
		);
	});

	it('calls pace alone only with no neutralisation behind it', () => {
		const h = new RaceHistoryReducer();
		h.update([
			lap('81', 1),
			lap('55', 1),
			gap('81', 1),
			gap('55', 3),
			lap('81', 2),
			lap('55', 2),
			gap('81', 1),
			gap('55', 2)
		]);
		expect(
			battleVerdict(
				battleTrend(h, '55', '81'),
				board('55', 'hard', 6),
				board('81', 'hard', 7),
				'Sainz',
				'Piastri'
			)
		).toBe('Down from 2.0 s on lap 1. Same tyres, same age: this one is on pace alone.');
	});

	it('names the fresher tyres', () => {
		expect(
			battleVerdict(null, board('55', 'medium', 4), board('81', 'medium', 12), 'Sainz', 'Piastri')
		).toBe("Sainz's tyres are 8 laps fresher.");
		expect(
			battleVerdict(null, board('55', 'soft', 10), board('81', 'hard', 2), 'Sainz', 'Piastri')
		).toBe("Piastri's hards are 8 laps fresher.");
	});
});

describe('ribbon and field line', () => {
	it('dashes the second car of a team by car number, never by place', () => {
		const rows = [
			row('81', { team: 'McLaren', position: 1 }),
			row('4', { team: 'McLaren', position: 2 }),
			row('1', { team: 'Red Bull Racing', position: 3 })
		];
		expect(teammateDashed(rows)).toEqual({ '1': false, '4': false, '81': true });
	});

	it('takes the running top ten, leaving retired cars out', () => {
		const h = new RaceHistoryReducer();
		h.update([pos('1', 1), pos('2', 2), lap('1', 1), lap('2', 1)]);
		const rows = [row('1', { position: 1 }), row('2', { position: 2, retired: true })];
		const model = ribbonModel(h, rows, { '1': 'VER' }, 57);
		expect(model.lines.map((l) => l.code)).toEqual(['VER']);
		expect(model.axisEnd).toBe(57);
		expect(model.reached).toBe(1);
	});

	it('says who leads and how spread the lead lap is', () => {
		const rows = [
			row('4', { position: 1 }),
			row('1', { position: 2, intervalToAheadS: 0.917, gapToLeaderS: 0.917 }),
			row('16', { position: 3, gapToLeaderS: 4.2 }),
			row('2', { position: 4, lapsDown: 1 }),
			row('3', { position: 5, retired: true })
		];
		expect(fieldLine(rows, { '4': 'NOR' })).toBe(
			'NOR leads by 0.9 s. 3 cars on the lead lap, covered by 4.2 s. 1 lapped.'
		);
		expect(fieldLine([], {})).toBeNull();
	});
});

describe('MomentToasts', () => {
	const explanation = (id: string, atMs: number, grounded = true): Envelope => ({
		event_time_utc: new Date(atMs).toISOString(),
		kind: 'explanation',
		payload: {
			anomaly_id: id,
			kind: 'position_change',
			lap_number: 12,
			drivers: ['4'],
			text: `Moment ${id}`,
			confidence: 'high',
			grounded,
			cited_evidence: []
		}
	});

	it('toasts a moment released near the clock, once, and never a backlog or a fallback', () => {
		const m = new MomentToasts();
		const now = 1_000_000;
		const events = [explanation('old', now - 60_000), explanation('weak', now - 1_000, false)];
		expect(m.take(events, now)).toEqual([]);
		events.push(explanation('new', now - 2_000));
		expect(m.take(events, now).map((t) => t.text)).toEqual(['Moment new']);
		expect(m.take(events, now)).toEqual([]);
	});

	it('toasts only the newest of a burst', () => {
		const m = new MomentToasts();
		const now = 1_000_000;
		const burst = [explanation('a', now - 3_000), explanation('b', now - 2_000)];
		expect(m.take(burst, now).map((t) => t.id)).toEqual(['b']);
	});
});
