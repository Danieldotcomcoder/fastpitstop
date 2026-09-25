import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { createDriverRow, type DriverRow } from '$lib/timing-tower';
import { focusPair } from './focus';
import { compoundLetter, fmtClock, fmtLap, sgn, surname } from './format';
import { LapRecordReducer } from './lap-records';
import { placeCars } from './placement';
import { buildBoard, crossoverFor, rejoinAfterStop, type Crossover } from './pit-board';
import { RaceFeedReducer, trackStatusChip } from './race-feed';
import { SnapshotHistory } from './snapshot-history';

function envelope(kind: string, payload: Record<string, unknown>): Envelope {
	return { event_time_utc: new Date(0).toISOString(), kind, payload };
}

function row(driver: string, overrides: Partial<DriverRow> = {}): DriverRow {
	return { ...createDriverRow(driver), code: `D${driver}`, ...overrides };
}

function crossover(overrides: Partial<Crossover> = {}): Crossover {
	return {
		kind: 'undercut',
		attacker: '4',
		defender: '1',
		window: 'opens_later',
		opens_in_laps: 2,
		confidence: 'medium',
		decision_lap: 20,
		attacker_stop_lap: 21,
		defender_stop_lap: null,
		crossover_lap: null,
		gap_s: 1.9,
		horizon_lap: 30,
		pit_loss_s: 22.4,
		pit_loss_provenance: 'calibrated',
		out_lap_penalty_s: 1.2,
		projected_gap_at_crossover_s: null,
		projected_gap_if_covered_s: -0.4,
		projected_gap_if_covered_low_s: -1.1,
		projected_gap_if_covered_high_s: 0.3,
		projected_margin_at_assumed_stop_s: null,
		projected_rejoin_gap_s: 3.2,
		reasons: [],
		attacker_state: { driver: '4' } as Crossover['attacker_state'],
		defender_state: { driver: '1' } as Crossover['defender_state'],
		...overrides
	} as Crossover;
}

const isCrossover = (v: unknown): v is Crossover =>
	typeof v === 'object' && v !== null && 'attacker' in v && 'window' in v;

describe('format', () => {
	it('formats lap times the way a timing screen does', () => {
		expect(fmtLap(92.418)).toBe('1:32.418');
		expect(fmtLap(65.05)).toBe('1:05.050');
		expect(fmtLap(null)).toBe('—');
	});
	it('signs deltas with a real minus sign', () => {
		expect(sgn(0.42)).toBe('+0.42');
		expect(sgn(-0.42)).toBe('−0.42');
		expect(sgn(0)).toBe('±0.00');
	});
	it('formats the race clock and compounds', () => {
		expect(fmtClock(3492)).toBe('0:58:12');
		expect(compoundLetter('MEDIUM')).toBe('M');
		expect(compoundLetter(null)).toBe('?');
	});
	it('finds the surname in either name style the feed uses', () => {
		expect(surname('Lando Norris')).toBe('Norris');
		expect(surname('Lando NORRIS')).toBe('Norris');
		expect(surname('Nyck DE VRIES')).toBe('De Vries');
	});
});

describe('LapRecordReducer', () => {
	it('colours the session best purple and a personal best green', () => {
		const records = new LapRecordReducer();
		records.update([
			envelope('lap_completed', { driver: '1', lap_time: 'PT1M31.5S' }),
			envelope('lap_completed', { driver: '4', lap_time: 'PT1M31.2S' }),
			envelope('lap_completed', { driver: '1', lap_time: 'PT1M31.4S' })
		]);
		expect(records.classify('4', 91.2)).toBe('fastest');
		expect(records.classify('1', 91.4)).toBe('pb');
		expect(records.classify('1', 91.5)).toBe('');
	});
	it('never marks a lap it has not been released yet', () => {
		const records = new LapRecordReducer();
		records.update([envelope('lap_completed', { driver: '1', lap_time: 'PT1M31.5S' })]);
		expect(records.classify('1', 91.5)).toBe('fastest');
		records.update([
			envelope('lap_completed', { driver: '1', lap_time: 'PT1M31.5S' }),
			envelope('lap_completed', { driver: '4', lap_time: 'PT1M30.9S' })
		]);
		expect(records.classify('1', 91.5)).toBe('pb');
	});
});

describe('focusPair', () => {
	const rows = [row('1', { position: 1 }), row('4', { position: 2 }), row('16', { position: 3 })];
	it('follows the fight for the lead by default', () => {
		expect(focusPair(rows, null)).toEqual({ a: '4', b: '1' });
	});
	it('puts the focused driver against the car ahead', () => {
		expect(focusPair(rows, '16')).toEqual({ a: '16', b: '4' });
	});
	it('makes second the attacker when the leader is focused', () => {
		expect(focusPair(rows, '1')).toEqual({ a: '4', b: '1' });
	});
	it('needs two placed cars', () => {
		expect(focusPair([row('1', { position: 1 })], null)).toBeNull();
	});
});

describe('pit board', () => {
	const rows = [
		row('1', { position: 1, gapToLeaderS: null }),
		row('4', { position: 2, gapToLeaderS: 1.9, intervalToAheadS: 1.9 }),
		row('16', { position: 3, gapToLeaderS: 20.0 }),
		row('63', { position: 4, gapToLeaderS: 26.0 })
	];
	const codes = { '1': 'VER', '4': 'NOR', '16': 'LEC', '63': 'RUS' };

	it('reads the window, margin and band straight off the kernel snapshot', () => {
		const board = buildBoard(rows, { a: '4', b: '1' }, crossover(), codes);
		expect(board.state).toBe('opens');
		expect(board.headline).toEqual({ before: 'Window opens in ', em: '2 laps', after: '' });
		expect(board.sub.em).toBe('0.40 s behind VER');
		expect([board.marginS, board.lowS, board.highS]).toEqual([-0.4, -1.1, 0.3]);
		expect(board.boxAtLap).toBe(23);
		expect(board.confidence).toBe('medium');
		expect(board.pitLossNote).toBe('Calibrated, this circuit');
		// The car ahead on rejoin is named the way the board shows drivers.
		expect(board.rejoin?.behind).toBe('LEC');
	});

	it('boxes on the kernel’s own assumed stop lap when the window is open', () => {
		const board = buildBoard(rows, { a: '4', b: '1' }, crossover({ window: 'open' }), codes);
		expect(board.state).toBe('open');
		expect(board.boxAtLap).toBe(21);
	});

	it('says so plainly when the kernel has no projection yet', () => {
		const board = buildBoard(rows, { a: '4', b: '1' }, null, codes);
		expect(board.state).toBe('unknown');
		expect(board.marginS).toBeNull();
	});

	it('shows the pit lane instead of a stale call while either car is stopping', () => {
		const pitting = rows.map((r) => (r.driver === '4' ? { ...r, inPit: true } : r));
		const board = buildBoard(pitting, { a: '4', b: '1' }, crossover(), codes);
		expect(board.state).toBe('pitting');
		expect(board.headline.em).toBe('NOR');
	});

	it('finds the undercut for exactly this pair', () => {
		const other = crossover({ attacker: '16', defender: '4' });
		const mine = crossover();
		expect(crossoverFor([other, mine], { a: '4', b: '1' })).toBe(mine);
		expect(crossoverFor([other], { a: '4', b: '1' })).toBeNull();
	});

	it('rejoins behind every car within its gap plus the pit loss', () => {
		// NOR at +1.9 plus 22.4 = 24.3: ahead of RUS (26.0), behind LEC (20.0).
		const rejoin = rejoinAfterStop(rows, '4', 22.4);
		expect(rejoin?.position).toBe(3);
		expect(rejoin?.behind).toBe('16');
		expect(rejoin?.gapS).toBeCloseTo(4.3);
		expect(rejoin?.inTraffic).toBe(false);
	});
});

describe('SnapshotHistory', () => {
	it('keeps the interval and the break-even line per lap for each pair', () => {
		const history = new SnapshotHistory();
		history.record({ crossovers: [crossover({ decision_lap: 19, gap_s: 2.2 })] }, isCrossover);
		history.record({ crossovers: [crossover({ decision_lap: 20, gap_s: 1.9 })] }, isCrossover);
		const points = history.points('4', '1');
		expect(points.map((p) => p.lap)).toEqual([19, 20]);
		// break-even interval = interval + margin
		expect(points[1]?.lineS).toBeCloseTo(1.5);
	});
	it('ignores overcuts and the same snapshot twice', () => {
		const history = new SnapshotHistory();
		const snapshot = { crossovers: [crossover({ kind: 'overcut' } as Partial<Crossover>)] };
		history.record(snapshot, isCrossover);
		history.record(snapshot, isCrossover);
		expect(history.points('4', '1')).toEqual([]);
	});
});

describe('race feed', () => {
	it('reads the latest released track status, chequered once finished', () => {
		const events = [
			envelope('track_status_changed', { status: 'vsc' }),
			envelope('lap_completed', { driver: '1' })
		];
		expect(trackStatusChip(events, false)).toEqual({ tone: 'vsc', label: 'VSC' });
		expect(trackStatusChip(events, true).tone).toBe('chequered');
		expect(trackStatusChip([], false).tone).toBe('green');
	});

	it('lists race control newest first, without blue-flag noise', () => {
		const feed = new RaceFeedReducer();
		feed.update([
			envelope('race_control_message', { message: 'DRS ENABLED', lap: 3 }),
			envelope('race_control_message', { message: 'WAVED BLUE FLAG FOR CAR 87', flag: 'BLUE' }),
			envelope('race_control_message', { message: 'VIRTUAL SAFETY CAR DEPLOYED', lap: 23 })
		]);
		expect(feed.list().map((item) => (item.type === 'rc' ? item.message : ''))).toEqual([
			'VIRTUAL SAFETY CAR DEPLOYED',
			'DRS ENABLED'
		]);
		expect(feed.list('radio')).toEqual([]);
	});
});

describe('placeCars', () => {
	const timing = (since: Record<string, number | null>, lap = 100) => ({
		lapsSinceLine: (d: string) => since[d] ?? null,
		lapTimeS: () => lap
	});

	it('spaces the field behind the leader by its timing-screen gaps', () => {
		const rows = [
			row('1', { position: 1 }),
			row('4', { position: 2, gapToLeaderS: 10 }),
			row('16', { position: 3, gapToLeaderS: 60 })
		];
		const at = placeCars(rows, timing({ '1': 0.5 }));
		expect(at.get('1')).toBeCloseTo(0.5);
		expect(at.get('4')).toBeCloseTo(0.4);
		expect(at.get('16')).toBeCloseTo(0.9); // wraps behind the line
	});

	it('keeps a late crossing moving instead of parking the car at the line', () => {
		// The 2026 Spanish GP replay: crossings arrived in bursts, so cars went
		// 1.5 laps without one and all piled up at start/finish.
		const at = placeCars([row('1', { position: 1 })], timing({ '1': 1.5 }));
		expect(at.get('1')).toBeCloseTo(0.5);
	});

	it('does not draw a car whose last crossing is laps overdue', () => {
		const rows = [row('1', { position: 1 }), row('44', { position: 2, lapsDown: 1 })];
		const at = placeCars(rows, timing({ '1': 0.2, '44': 9 }));
		expect(at.has('44')).toBe(false);
	});
});
