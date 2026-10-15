import { describe, expect, it } from 'vitest';
import type { components } from '$lib/api/schema';
import { calendar, calendarSummary, weekends } from './weekends';

type SessionSummary = components['schemas']['SessionSummaryOut'];
type ScheduleEvent = components['schemas']['ScheduleEventOut'];

function s(round: number, type: string, extra: Partial<SessionSummary> = {}): SessionSummary {
	return {
		key: `2026-${round}-${type}`,
		session_key: { year: 2026, round, session_type: type },
		event_name: round === 17 ? 'Singapore Grand Prix' : 'Azerbaijan Grand Prix',
		provenance: 'historical',
		circuit_name: round === 17 ? 'Marina Bay Street Circuit' : 'Baku City Circuit',
		winner: null,
		has_radio: false,
		has_telemetry: false,
		live_recorded: false,
		live: false,
		...extra
	} as SessionSummary;
}

describe('weekends', () => {
	it('groups a round, newest first, its sessions in running order', () => {
		const out = weekends(
			[
				s(16, 'race'),
				s(17, 'race', { live_recorded: true }),
				s(17, 'qualifying'),
				s(17, 'sprint'),
				s(17, 'practice_1'),
				s(17, 'sprint_qualifying'),
				{ ...s(17, 'race'), session_key: { year: 2025, round: 17, session_type: 'race' } }
			],
			2026
		);
		expect(out.map((w) => w.round)).toEqual([17, 16]);
		expect(out[0]?.sessions.map((x) => x.label)).toEqual([
			'Practice 1',
			'Sprint Qualifying',
			'Sprint',
			'Qualifying',
			'Race'
		]);
		expect(out[0]?.sessions.at(-1)?.href).toBe('/sessions/2026/17');
		expect(out[0]?.sessions[0]?.href).toBe('/sessions/2026/17?session=practice_1');
		expect(out[0]?.liveRecorded).toBe(true);
	});

	it('sends a session still running to /live', () => {
		expect(weekends([s(17, 'race', { live: true })], 2026)[0]?.sessions[0]?.href).toBe('/live');
	});
});

describe('calendar', () => {
	const ev = (round: number, name: string, raceUtc: string): ScheduleEvent =>
		({
			season: 2026,
			round,
			event_name: name,
			event_format: 'conventional',
			circuit_id: null,
			circuit_name: null,
			sessions: [{ name: 'Race', session_type: 'race', start_utc: raceUtc, start_local: null }]
		}) as unknown as ScheduleEvent;
	const events = [
		ev(2, 'Saudi Arabian Grand Prix', '2026-03-08T17:00:00Z'),
		ev(1, 'Bahrain Grand Prix', '2026-03-01T15:00:00Z'),
		ev(3, 'United States Grand Prix', '2026-10-25T19:00:00Z'),
		ev(4, 'Mexico City Grand Prix', '2026-11-01T20:00:00Z'),
		{
			...ev(0, 'Pre-Season Testing', '2026-02-20T08:00:00Z'),
			event_format: 'testing'
		} as ScheduleEvent
	];

	it('marks rounds run, the next one, and links replays or Grand Prix pages', () => {
		const out = calendar(events, [s(1, 'race')], Date.parse('2026-10-15T00:00:00Z'));
		expect(out.map((r) => [r.round, r.state])).toEqual([
			[1, 'run'],
			[2, 'run'],
			[3, 'next'],
			[4, 'later']
		]);
		expect(out[0]?.href).toBe('/sessions/2026/1');
		expect(out.map((r) => r.held)).toEqual([true, false, false, false]);
		expect(out[2]?.href).toBe('/races/2026/united-states');
		expect(calendarSummary(out)).toBe('2 of 4 rounds run, round 3 next');
	});
});
