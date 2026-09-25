import { describe, expect, it } from 'vitest';
import {
	buildRaceShapeSeries,
	latestPosition,
	maxGapToLeader,
	maxLapNumber,
	maxPosition,
	pitMarkers,
	teammateDashOrder,
	trackStatusBands
} from './race-shape';
import type { components } from '$lib/api/client';

type LapRow = components['schemas']['LapRowOut'];

function lap(overrides: Partial<LapRow>): LapRow {
	return {
		session_year: 2024,
		session_round: 1,
		session_type: 'race',
		driver: '1',
		lap_number: 1,
		lap_start_utc: null,
		lap_end_utc: null,
		lap_time_s: 90,
		sector1_s: null,
		sector2_s: null,
		sector3_s: null,
		is_personal_best: null,
		compound: 'soft',
		tyre_age: 1,
		stint_number: 1,
		position: 1,
		gap_ahead_s: null,
		gap_ahead_min_s: null,
		gap_ahead_samples: null,
		gap_to_leader_s: 0,
		track_status: 'green',
		track_status_flags: [],
		is_pit_in: false,
		is_pit_out: false,
		provenance: 'historical',
		is_lap1: false,
		is_in_lap: false,
		is_out_lap: false,
		is_sc: false,
		is_vsc: false,
		is_yellow: false,
		is_red: false,
		is_dirty_air: false,
		is_anomalous: false,
		dirty_air_known: true,
		anomaly_known: true,
		is_clean: true,
		exclusion_reasons: [],
		...overrides
	};
}

describe('buildRaceShapeSeries', () => {
	it('only includes selected drivers, sorted by lap number', () => {
		const laps = [
			lap({ driver: '1', lap_number: 2, gap_to_leader_s: 5 }),
			lap({ driver: '1', lap_number: 1, gap_to_leader_s: 0 }),
			lap({ driver: '44', lap_number: 1, gap_to_leader_s: 2 })
		];
		const series = buildRaceShapeSeries(laps, ['1']);
		expect(series).toHaveLength(1);
		const [only] = series;
		expect(only?.driver).toBe('1');
		expect(only?.points.map((p) => p.lapNumber)).toEqual([1, 2]);
	});

	it('omits a selected driver with no laps rather than an empty series', () => {
		const series = buildRaceShapeSeries([lap({ driver: '1' })], ['1', '99']);
		expect(series.map((s) => s.driver)).toEqual(['1']);
	});
});

describe('axis helpers', () => {
	const series = buildRaceShapeSeries(
		[
			lap({ driver: '1', lap_number: 1, gap_to_leader_s: 0, position: 1 }),
			lap({ driver: '44', lap_number: 1, gap_to_leader_s: 8.5, position: 3 })
		],
		['1', '44']
	);

	it('maxLapNumber finds the highest lap across all series', () => {
		expect(maxLapNumber(series)).toBe(1);
	});

	it('maxGapToLeader finds the largest gap', () => {
		expect(maxGapToLeader(series)).toBe(8.5);
	});

	it('maxPosition finds the worst position', () => {
		expect(maxPosition(series)).toBe(3);
	});

	it('returns null when nothing has a gap yet', () => {
		const empty = buildRaceShapeSeries([lap({ driver: '1', gap_to_leader_s: null })], ['1']);
		expect(maxGapToLeader(empty)).toBeNull();
	});
});

describe('trackStatusBands', () => {
	it('merges consecutive laps of the same status into one band', () => {
		const bands = trackStatusBands([
			lap({ driver: '1', lap_number: 5, is_sc: true }),
			lap({ driver: '1', lap_number: 6, is_sc: true }),
			lap({ driver: '1', lap_number: 7, is_sc: true })
		]);
		expect(bands).toEqual([{ fromLap: 5, toLap: 7, status: 'sc' }]);
	});

	it('separates bands that are not adjacent', () => {
		const bands = trackStatusBands([
			lap({ driver: '1', lap_number: 2, is_vsc: true }),
			lap({ driver: '1', lap_number: 9, is_vsc: true })
		]);
		expect(bands).toEqual([
			{ fromLap: 2, toLap: 2, status: 'vsc' },
			{ fromLap: 9, toLap: 9, status: 'vsc' }
		]);
	});

	it('takes the most severe status when the field disagrees on one lap', () => {
		// A driver in the pit lane can miss a flag their rivals saw; the lap
		// was still neutralised for the race.
		const bands = trackStatusBands([
			lap({ driver: '1', lap_number: 4, is_vsc: true }),
			lap({ driver: '44', lap_number: 4, is_sc: true })
		]);
		expect(bands).toEqual([{ fromLap: 4, toLap: 4, status: 'sc' }]);
	});

	it('returns nothing for a fully green race', () => {
		expect(trackStatusBands([lap({ driver: '1', lap_number: 1 })])).toEqual([]);
	});
});

describe('pitMarkers', () => {
	it('marks only the lap a driver actually entered the pits', () => {
		const series = buildRaceShapeSeries(
			[
				lap({ driver: '1', lap_number: 1 }),
				lap({ driver: '1', lap_number: 2, is_pit_in: true }),
				lap({ driver: '1', lap_number: 3, is_pit_out: true })
			],
			['1']
		);
		expect(pitMarkers(series)).toEqual([{ driver: '1', lapNumber: 2 }]);
	});
});

describe('teammateDashOrder', () => {
	const teams: Record<string, string | null> = {
		'1': 'Red Bull Racing',
		'11': 'Red Bull Racing',
		'16': 'Ferrari',
		'99': null
	};
	const teamOf = (d: string) => teams[d] ?? null;

	it('dashes the second car of a team and not the first', () => {
		const dashed = teammateDashOrder(['1', '11', '16'], teamOf);
		expect(dashed['1']).toBe(false);
		expect(dashed['11']).toBe(true);
		expect(dashed['16']).toBe(false);
	});

	it('follows the order given, so the car ahead is the solid one', () => {
		const dashed = teammateDashOrder(['11', '1'], teamOf);
		expect(dashed['11']).toBe(false);
		expect(dashed['1']).toBe(true);
	});

	it('never dashes a driver whose team is unknown', () => {
		const dashed = teammateDashOrder(['99', '1'], teamOf);
		expect(dashed['99']).toBe(false);
	});
});

describe('latestPosition', () => {
	it('takes the last lap that actually carried a position', () => {
		const [shape] = buildRaceShapeSeries(
			[
				lap({ driver: '1', lap_number: 1, position: 4 }),
				lap({ driver: '1', lap_number: 2, position: 2 }),
				lap({ driver: '1', lap_number: 3, position: null })
			],
			['1']
		);
		expect(latestPosition(shape!)).toBe(2);
	});

	it('is null when no lap ever carried one', () => {
		const [shape] = buildRaceShapeSeries([lap({ driver: '1', position: null })], ['1']);
		expect(latestPosition(shape!)).toBeNull();
	});
});
