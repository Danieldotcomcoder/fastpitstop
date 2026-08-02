import { describe, expect, it } from 'vitest';
import { buildPitStops, buildStints, maxStintLap } from './strategy';
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

describe('buildStints', () => {
	it('groups consecutive laps of the same stint into one bar', () => {
		const laps = [
			lap({ lap_number: 1, stint_number: 1, compound: 'soft' }),
			lap({ lap_number: 2, stint_number: 1, compound: 'soft' }),
			lap({ lap_number: 3, stint_number: 2, compound: 'hard' })
		];
		const stints = buildStints(laps, ['1']);
		expect(stints).toEqual([
			{ driver: '1', stintNumber: 1, compound: 'soft', startLap: 1, endLap: 2, lapCount: 2 },
			{ driver: '1', stintNumber: 2, compound: 'hard', startLap: 3, endLap: 3, lapCount: 1 }
		]);
	});

	it('keeps two drivers separate even if stint numbers repeat', () => {
		const laps = [
			lap({ driver: '1', lap_number: 1, stint_number: 1 }),
			lap({ driver: '44', lap_number: 1, stint_number: 1 })
		];
		const stints = buildStints(laps, ['1', '44']);
		expect(stints.map((s) => s.driver)).toEqual(['1', '44']);
	});

	it('excludes laps with no stint number rather than crashing', () => {
		const laps = [lap({ lap_number: 1, stint_number: null })];
		expect(buildStints(laps, ['1'])).toEqual([]);
	});
});

describe('buildPitStops', () => {
	it('collects only pit-in laps for selected drivers', () => {
		const laps = [
			lap({ driver: '1', lap_number: 15, is_pit_in: true }),
			lap({ driver: '1', lap_number: 14, is_pit_in: false }),
			lap({ driver: '99', lap_number: 15, is_pit_in: true })
		];
		expect(buildPitStops(laps, ['1'])).toEqual([{ driver: '1', lapNumber: 15 }]);
	});
});

describe('maxStintLap', () => {
	it('finds the last lap across every stint', () => {
		const stints = buildStints(
			[lap({ lap_number: 1, stint_number: 1 }), lap({ lap_number: 20, stint_number: 2 })],
			['1']
		);
		expect(maxStintLap(stints)).toBe(20);
	});
});
