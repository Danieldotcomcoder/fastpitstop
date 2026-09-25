import { describe, expect, it } from 'vitest';
import {
	bestDriftForCorner,
	buildCornerBraking,
	driftDirection,
	headlineCorner
} from './braking';
import type { components } from '$lib/api/client';

type BrakingPoint = components['schemas']['BrakingPointRowOut'];
type BrakingDrift = components['schemas']['BrakingDriftOut'];

function point(overrides: Partial<BrakingPoint>): BrakingPoint {
	return {
		session_year: 2024,
		session_round: 1,
		session_type: 'race',
		driver: '1',
		lap_number: 1,
		corner_number: 1,
		corner_letter: null,
		corner_distance_m: 700,
		brake_point_distance_m: 650,
		distance_before_corner_m: 50,
		n_samples_in_window: 20,
		known: true,
		...overrides
	};
}

function drift(overrides: Partial<BrakingDrift>): BrakingDrift {
	return {
		session_year: 2024,
		session_round: 1,
		session_type: 'race',
		driver: '1',
		stint_number: 1,
		compound: 'soft',
		corner_number: 1,
		corner_letter: null,
		drift_m_per_lap: 1,
		drift_ci_low_m_per_lap: 0.5,
		drift_ci_high_m_per_lap: 1.5,
		intercept_m: 50,
		n_laps_used: 10,
		reliability: 'good',
		...overrides
	};
}

describe('buildCornerBraking', () => {
	it('summarises one row per corner with a median, spread and lap count', () => {
		const rows = buildCornerBraking(
			[
				point({ lap_number: 1, corner_number: 1, distance_before_corner_m: 40 }),
				point({ lap_number: 2, corner_number: 1, distance_before_corner_m: 50 }),
				point({ lap_number: 3, corner_number: 1, distance_before_corner_m: 60 })
			],
			[],
			3
		);
		expect(rows).toHaveLength(1);
		expect(rows[0]?.medianDistanceM).toBe(50);
		expect(rows[0]?.minDistanceM).toBe(40);
		expect(rows[0]?.maxDistanceM).toBe(60);
		expect(rows[0]?.lapsDetected).toBe(3);
	});

	it('drops a corner with too few detections rather than showing a noisy one', () => {
		const rows = buildCornerBraking(
			[point({ corner_number: 7, distance_before_corner_m: 30 })],
			[],
			3
		);
		expect(rows).toEqual([]);
	});

	it('counts a flat-out corner as "no braking", not as missing data', () => {
		// known=true with a null distance is the kernel saying "plenty of
		// telemetry, genuinely no sustained braking here" — a real fact.
		const rows = buildCornerBraking(
			[
				point({ lap_number: 1, corner_number: 3, distance_before_corner_m: 20 }),
				point({ lap_number: 2, corner_number: 3, distance_before_corner_m: 22 }),
				point({ lap_number: 3, corner_number: 3, distance_before_corner_m: 21 }),
				point({ lap_number: 4, corner_number: 3, distance_before_corner_m: null, known: true })
			],
			[],
			3
		);
		expect(rows[0]?.lapsWithoutBraking).toBe(1);
		expect(rows[0]?.lapsDetected).toBe(3);
	});

	it('ignores laps where there was not enough telemetry to judge', () => {
		const rows = buildCornerBraking(
			[
				point({ lap_number: 1, corner_number: 3, distance_before_corner_m: 20 }),
				point({ lap_number: 2, corner_number: 3, distance_before_corner_m: 22 }),
				point({ lap_number: 3, corner_number: 3, distance_before_corner_m: 21 }),
				point({ lap_number: 4, corner_number: 3, distance_before_corner_m: null, known: false })
			],
			[],
			3
		);
		expect(rows[0]?.lapsWithoutBraking).toBe(0);
	});

	it('orders rows around the lap', () => {
		const many = (corner: number) =>
			[1, 2, 3].map((lap) =>
				point({ lap_number: lap, corner_number: corner, distance_before_corner_m: 40 })
			);
		const rows = buildCornerBraking([...many(9), ...many(2), ...many(14)], [], 3);
		expect(rows.map((r) => r.cornerNumber)).toEqual([2, 9, 14]);
	});

	it('attaches the corner’s own fitted trend', () => {
		const rows = buildCornerBraking(
			[1, 2, 3].map((lap) => point({ lap_number: lap, distance_before_corner_m: 40 })),
			[drift({ corner_number: 1, drift_m_per_lap: 2.5 })],
			3
		);
		expect(rows[0]?.drift?.drift_m_per_lap).toBe(2.5);
	});
});

describe('bestDriftForCorner', () => {
	it('prefers a more reliable fit over one with more laps', () => {
		const best = bestDriftForCorner([
			drift({ reliability: 'thin', n_laps_used: 30 }),
			drift({ reliability: 'good', n_laps_used: 8 })
		]);
		expect(best?.reliability).toBe('good');
	});

	it('breaks a reliability tie on lap count', () => {
		const best = bestDriftForCorner([
			drift({ reliability: 'good', n_laps_used: 8, stint_number: 1 }),
			drift({ reliability: 'good', n_laps_used: 19, stint_number: 2 })
		]);
		expect(best?.stint_number).toBe(2);
	});

	it('is null when no stint produced a fit', () => {
		expect(bestDriftForCorner([])).toBeNull();
	});
});

describe('driftDirection', () => {
	it('reads a positive, sign-stable trend as braking earlier', () => {
		expect(
			driftDirection(
				drift({ drift_m_per_lap: 2, drift_ci_low_m_per_lap: 1, drift_ci_high_m_per_lap: 3 })
			)
		).toBe('earlier');
	});

	it('reads a negative, sign-stable trend as braking later', () => {
		expect(
			driftDirection(
				drift({ drift_m_per_lap: -2, drift_ci_low_m_per_lap: -3, drift_ci_high_m_per_lap: -1 })
			)
		).toBe('later');
	});

	it('calls a confidence interval that spans zero "steady", never a direction', () => {
		// The false-precision guard: a positive point estimate whose own CI
		// crosses zero is not evidence of a direction.
		expect(
			driftDirection(
				drift({ drift_m_per_lap: 0.4, drift_ci_low_m_per_lap: -2, drift_ci_high_m_per_lap: 3 })
			)
		).toBe('steady');
	});

	it('never reports a direction from an unreliable fit', () => {
		expect(
			driftDirection(
				drift({
					reliability: 'unreliable',
					drift_m_per_lap: 5,
					drift_ci_low_m_per_lap: 4,
					drift_ci_high_m_per_lap: 6
				})
			)
		).toBe('unknown');
	});

	it('is unknown with no fit at all', () => {
		expect(driftDirection(null)).toBe('unknown');
	});
});

describe('headlineCorner', () => {
	const rows = (fits: (BrakingDrift | null)[]) =>
		fits.map((d, i) => ({
			cornerNumber: i + 1,
			cornerLetter: null,
			medianDistanceM: 50,
			minDistanceM: 40,
			maxDistanceM: 60,
			lapsDetected: 10,
			lapsWithoutBraking: 0,
			drift: d
		}));

	it('picks the largest directional movement', () => {
		const best = headlineCorner(
			rows([
				drift({ drift_m_per_lap: 1, drift_ci_low_m_per_lap: 0.5, drift_ci_high_m_per_lap: 1.5 }),
				drift({ drift_m_per_lap: -4, drift_ci_low_m_per_lap: -5, drift_ci_high_m_per_lap: -3 })
			])
		);
		expect(best?.cornerNumber).toBe(2);
	});

	it('ignores corners with no directional evidence', () => {
		const best = headlineCorner(
			rows([
				drift({ drift_m_per_lap: 9, drift_ci_low_m_per_lap: -9, drift_ci_high_m_per_lap: 20 }),
				null
			])
		);
		expect(best).toBeNull();
	});
});
