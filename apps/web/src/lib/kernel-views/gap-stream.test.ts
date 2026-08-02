import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { GapStreamReducer } from './gap-stream';

function envelope(kind: string, payload: Record<string, unknown>, tsIso: string): Envelope {
	return { event_time_utc: tsIso, kind, payload };
}

describe('GapStreamReducer', () => {
	it('folds driver_list_updated into code/team', () => {
		const reducer = new GapStreamReducer();
		reducer.update([
			envelope(
				'driver_list_updated',
				{ drivers: [{ number: '44', code: 'HAM', team: 'Mercedes' }] },
				'2024-01-01T00:00:00Z'
			)
		]);
		const series = reducer.state.drivers.get('44');
		expect(series?.code).toBe('HAM');
		expect(series?.team).toBe('Mercedes');
	});

	it("tags each gap sample with the driver's own most recent completed lap", () => {
		const reducer = new GapStreamReducer();
		reducer.update([
			envelope('gap_updated', { driver: '44', gap_to_leader: 'PT1S' }, '2024-01-01T00:00:00Z'),
			envelope('lap_completed', { driver: '44', lap_number: 1 }, '2024-01-01T00:01:00Z'),
			envelope('gap_updated', { driver: '44', gap_to_leader: 'PT2S' }, '2024-01-01T00:01:30Z')
		]);
		const samples = reducer.state.drivers.get('44')?.samples ?? [];
		expect(samples[0]?.lapNumber).toBeNull();
		expect(samples[0]?.gapToLeaderS).toBeCloseTo(1, 6);
		expect(samples[1]?.lapNumber).toBe(1);
		expect(samples[1]?.gapToLeaderS).toBeCloseTo(2, 6);
	});

	it('records laps_down and a null gap when lapped', () => {
		const reducer = new GapStreamReducer();
		reducer.update([
			envelope('gap_updated', { driver: '20', laps_down: 1 }, '2024-01-01T00:00:00Z')
		]);
		const sample = reducer.state.drivers.get('20')?.samples[0];
		expect(sample?.gapToLeaderS).toBeNull();
		expect(sample?.lapsDown).toBe(1);
	});

	it('only processes newly-appended events on each update', () => {
		const reducer = new GapStreamReducer();
		const first = [
			envelope('gap_updated', { driver: '44', gap_to_leader: 'PT1S' }, '2024-01-01T00:00:00Z')
		];
		reducer.update(first);
		const second = [
			...first,
			envelope('gap_updated', { driver: '44', gap_to_leader: 'PT2S' }, '2024-01-01T00:00:05Z')
		];
		reducer.update(second);
		expect(reducer.state.drivers.get('44')?.samples).toHaveLength(2);
	});

	it('reset clears all accumulated series', () => {
		const reducer = new GapStreamReducer();
		reducer.update([
			envelope('gap_updated', { driver: '44', gap_to_leader: 'PT1S' }, '2024-01-01T00:00:00Z')
		]);
		reducer.reset();
		expect(reducer.state.drivers.size).toBe(0);
	});
});
