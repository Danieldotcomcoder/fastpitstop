import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import {
	applyTowerEnvelope,
	createTimingTowerState,
	sortedRows,
	TimingTowerReducer
} from './reducer';
import { positionsGained, stintsForBar } from './types';

function envelope(kind: string, payload: Record<string, unknown>): Envelope {
	return { event_time_utc: new Date(0).toISOString(), kind, payload };
}

describe('applyTowerEnvelope: driver_list_updated', () => {
	it('sets code/team and creates a row with every other field unknown', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('driver_list_updated', {
				drivers: [{ number: '44', code: 'HAM', team: 'Mercedes' }]
			})
		);
		const row = state.rows.get('44');
		expect(row?.code).toBe('HAM');
		expect(row?.team).toBe('Mercedes');
		expect(row?.position).toBeNull();
		expect(row?.inPit).toBe(false);
	});

	it('merges onto an existing row without clobbering known fields', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '44', position: 3 }));
		applyTowerEnvelope(
			state,
			envelope('driver_list_updated', { drivers: [{ number: '44', code: 'HAM' }] })
		);
		expect(state.rows.get('44')?.position).toBe(3);
		expect(state.rows.get('44')?.code).toBe('HAM');
	});

	it('ignores malformed driver entries', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('driver_list_updated', { drivers: [{ code: 'HAM' }] }));
		expect(state.rows.size).toBe(0);
	});
});

describe('applyTowerEnvelope: position_changed', () => {
	it('sets position, leaving it unset when the payload omits it', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 1 }));
		expect(state.rows.get('1')?.position).toBe(1);

		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: null }));
		expect(state.rows.get('1')?.position).toBe(1);
	});
});

describe('applyTowerEnvelope: gap_updated', () => {
	it('parses the ISO-duration gap and interval, and a lapped count', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('gap_updated', {
				driver: '44',
				gap_to_leader: 'PT12.345S',
				interval_to_ahead: 'PT1.2S',
				laps_down: null
			})
		);
		const row = state.rows.get('44')!;
		expect(row.gapToLeaderS).toBeCloseTo(12.345);
		expect(row.intervalToAheadS).toBeCloseTo(1.2);
		expect(row.lapsDown).toBeNull();
	});

	it('a lapped driver carries lapsDown with gapToLeaderS null', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('gap_updated', { driver: '2', gap_to_leader: null, laps_down: 1 })
		);
		const row = state.rows.get('2')!;
		expect(row.gapToLeaderS).toBeNull();
		expect(row.lapsDown).toBe(1);
	});

	it('a later unknown gap overwrites a previously known one (never sticky)', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('gap_updated', { driver: '44', gap_to_leader: 'PT5S' }));
		applyTowerEnvelope(state, envelope('gap_updated', { driver: '44', gap_to_leader: null }));
		expect(state.rows.get('44')?.gapToLeaderS).toBeNull();
	});
});

describe('applyTowerEnvelope: lap_completed', () => {
	it('updates lap number, last lap time, personal-best flag, compound and tyre age', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('lap_completed', {
				driver: '44',
				lap_number: 12,
				lap_time: 'PT1M32.456S',
				is_personal_best: true,
				compound: 'medium',
				tyre_age: 11
			})
		);
		const row = state.rows.get('44')!;
		expect(row.lapNumber).toBe(12);
		expect(row.lastLapTimeS).toBeCloseTo(92.456);
		expect(row.isPersonalBest).toBe(true);
		expect(row.compound).toBe('medium');
		expect(row.tyreAge).toBe(11);
	});

	it('never regresses a known field back to unknown on a sparser update', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('lap_completed', { driver: '44', lap_number: 1, compound: 'soft', tyre_age: 1 })
		);
		applyTowerEnvelope(state, envelope('lap_completed', { driver: '44', lap_number: 2 }));
		const row = state.rows.get('44')!;
		expect(row.lapNumber).toBe(2);
		expect(row.compound).toBe('soft');
		expect(row.tyreAge).toBe(1);
	});
});

describe('applyTowerEnvelope: stint_started', () => {
	it('sets stint number, compound and the tyre-age baseline', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('stint_started', {
				driver: '44',
				stint_number: 2,
				compound: 'hard',
				start_lap: 15,
				tyre_age_at_start: 0
			})
		);
		const row = state.rows.get('44')!;
		expect(row.stintNumber).toBe(2);
		expect(row.compound).toBe('hard');
		expect(row.tyreAge).toBe(0);
	});

	it('a fresh stint resets tyre age even though it is falsy (0)', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('lap_completed', { driver: '44', lap_number: 20, compound: 'soft', tyre_age: 19 })
		);
		applyTowerEnvelope(
			state,
			envelope('stint_started', {
				driver: '44',
				stint_number: 3,
				compound: 'medium',
				tyre_age_at_start: 0
			})
		);
		expect(state.rows.get('44')?.tyreAge).toBe(0);
	});
});

describe('applyTowerEnvelope: pit_entry / pit_exit', () => {
	it('toggles inPit true on entry and false on the matching exit', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('pit_entry', { driver: '44', lap_number: 15 }));
		expect(state.rows.get('44')?.inPit).toBe(true);

		applyTowerEnvelope(state, envelope('pit_exit', { driver: '44', lap_number: 16 }));
		expect(state.rows.get('44')?.inPit).toBe(false);
	});
});

describe('applyTowerEnvelope: unrelated kinds', () => {
	it('is a no-op for an event with no driver row concept', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('weather_sample', { air_temp: 25 }));
		expect(state.rows.size).toBe(0);
	});
});

describe('sortedRows', () => {
	it('orders classified drivers by position ascending', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '44', position: 2 }));
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 1 }));
		applyTowerEnvelope(state, envelope('position_changed', { driver: '16', position: 3 }));
		expect(sortedRows(state).map((r) => r.driver)).toEqual(['1', '44', '16']);
	});

	it('sorts unclassified drivers after every classified one, by driver number', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 1 }));
		applyTowerEnvelope(state, envelope('driver_list_updated', { drivers: [{ number: '44' }] }));
		applyTowerEnvelope(state, envelope('driver_list_updated', { drivers: [{ number: '2' }] }));
		expect(sortedRows(state).map((r) => r.driver)).toEqual(['1', '2', '44']);
	});
});

describe('TimingTowerReducer', () => {
	it('only processes newly appended events on each update (cursor-based fold)', () => {
		const reducer = new TimingTowerReducer();
		const events: Envelope[] = [envelope('position_changed', { driver: '1', position: 1 })];
		reducer.update(events);
		expect(reducer.rows).toHaveLength(1);

		events.push(envelope('position_changed', { driver: '44', position: 2 }));
		reducer.update(events);
		expect(reducer.rows.map((r) => r.driver)).toEqual(['1', '44']);
	});

	it('reset clears rows and the cursor', () => {
		const reducer = new TimingTowerReducer();
		reducer.update([envelope('position_changed', { driver: '1', position: 1 })]);
		reducer.reset();
		expect(reducer.rows).toHaveLength(0);
	});
});

describe('start position and places gained', () => {
	it('latches the first position seen and never revises it', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 8 }));
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 3 }));
		applyTowerEnvelope(state, envelope('position_changed', { driver: '1', position: 5 }));
		const row = state.rows.get('1')!;
		expect(row.startPosition).toBe(8);
		expect(row.position).toBe(5);
		expect(positionsGained(row)).toBe(3);
	});

	it('reports a loss as a negative number', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '4', position: 2 }));
		applyTowerEnvelope(state, envelope('position_changed', { driver: '4', position: 7 }));
		expect(positionsGained(state.rows.get('4')!)).toBe(-5);
	});

	it('distinguishes "held station" (0) from unknown (null)', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(state, envelope('position_changed', { driver: '9', position: 6 }));
		expect(positionsGained(state.rows.get('9')!)).toBe(0);

		// A driver the feed has only ever mentioned via a lap time has no
		// position at either end — that is unknown, not "no change".
		applyTowerEnvelope(state, envelope('lap_completed', { driver: '10', lap_number: 3 }));
		expect(positionsGained(state.rows.get('10')!)).toBeNull();
	});
});

describe('best lap and sectors', () => {
	it("keeps the fastest lap so far and the last lap's sectors", () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('lap_completed', {
				driver: '4',
				lap_number: 5,
				lap_time: 'PT1M31.5S',
				sector_times: ['PT30.1S', 'PT31.2S', 'PT30.2S']
			})
		);
		applyTowerEnvelope(
			state,
			envelope('lap_completed', {
				driver: '4',
				lap_number: 6,
				lap_time: 'PT1M32.0S',
				sector_times: ['PT30.4S', null, 'PT30.3S']
			})
		);
		const row = state.rows.get('4')!;
		expect(row.bestLapS).toBeCloseTo(91.5);
		expect(row.lastLapTimeS).toBeCloseTo(92.0);
		expect(row.lastSectorsS?.[0]).toBeCloseTo(30.4);
		expect(row.lastSectorsS?.[1]).toBeNull();
		expect(row.lastSectorsS?.[2]).toBeCloseTo(30.3);
	});

	it('keeps the last sectors when a later lap carries none', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('lap_completed', {
				driver: '4',
				lap_number: 1,
				sector_times: ['PT30S', 'PT31S', 'PT30S']
			})
		);
		applyTowerEnvelope(state, envelope('lap_completed', { driver: '4', lap_number: 2 }));
		expect(state.rows.get('4')?.lastSectorsS).toHaveLength(3);
	});
});

describe('stint history', () => {
	it('records each stint once, in order, and ends each where the next begins', () => {
		const state = createTimingTowerState();
		const stint = (n: number, compound: string, start: number | null) =>
			envelope('stint_started', { driver: '4', stint_number: n, compound, start_lap: start });
		applyTowerEnvelope(state, stint(2, 'hard', 28));
		applyTowerEnvelope(state, stint(1, 'medium', 1));
		applyTowerEnvelope(state, stint(2, 'hard', 28)); // re-sent: no duplicate
		const row = state.rows.get('4')!;
		expect(row.stints.map((s) => s.stintNumber)).toEqual([1, 2]);
		expect(stintsForBar(row)).toEqual([
			{ compound: 'medium', startLap: 1, endLap: 27 },
			{ compound: 'hard', startLap: 28, endLap: null }
		]);
	});

	it('starts a stint with no given start lap after the previous one', () => {
		const state = createTimingTowerState();
		applyTowerEnvelope(
			state,
			envelope('stint_started', { driver: '4', stint_number: 1, compound: 'soft', start_lap: null })
		);
		applyTowerEnvelope(
			state,
			envelope('stint_started', { driver: '4', stint_number: 2, compound: 'hard', start_lap: 20 })
		);
		expect(stintsForBar(state.rows.get('4')!)).toEqual([
			{ compound: 'soft', startLap: 1, endLap: 19 },
			{ compound: 'hard', startLap: 20, endLap: null }
		]);
	});
});
