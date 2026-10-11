import { describe, expect, it } from 'vitest';
import {
	countdownLabel,
	nextSession,
	sessionPhase,
	trackTime,
	weekendDates,
	type ScheduleEvent
} from './next-race';

// The 2026 United States GP, as GET /schedule/next returns it.
const austin: ScheduleEvent = {
	season: 2026,
	round: 18,
	event_name: 'United States Grand Prix',
	location: 'Austin',
	country: 'United States',
	event_format: 'conventional',
	circuit_id: 'cota',
	circuit_name: 'Circuit of the Americas',
	sessions: [
		{
			name: 'Practice 1',
			session_type: 'practice_1',
			start_utc: '2026-10-23T17:30:00Z',
			start_local: '2026-10-23T12:30:00-05:00'
		},
		{
			name: 'Qualifying',
			session_type: 'qualifying',
			start_utc: '2026-10-24T21:00:00Z',
			start_local: '2026-10-24T16:00:00-05:00'
		},
		{
			name: 'Race',
			session_type: 'race',
			start_utc: '2026-10-25T20:00:00Z',
			start_local: '2026-10-25T15:00:00-05:00'
		}
	]
};
const at = (iso: string) => new Date(iso).getTime();

describe('trackTime', () => {
	it('reads the time at the track, wherever this runs', () => {
		expect(trackTime(austin.sessions[2]!)).toBe('Sun 15:00');
		expect(trackTime(austin.sessions[0]!)).toBe('Fri 12:30');
	});

	it('falls back to UTC, marked, without a local time', () => {
		expect(trackTime({ ...austin.sessions[2]!, start_local: null })).toBe('Sun 20:00 UTC');
	});
});

describe('weekendDates', () => {
	it('spans the weekend at the track', () => {
		expect(weekendDates(austin)).toBe('23–25 Oct');
	});

	it('spans a month boundary', () => {
		const mexico = {
			...austin,
			sessions: [
				{ ...austin.sessions[0]!, start_local: '2026-10-30T12:30:00-06:00' },
				{ ...austin.sessions[2]!, start_local: '2026-11-01T14:00:00-06:00' }
			]
		};
		expect(weekendDates(mexico)).toBe('30 Oct – 1 Nov');
	});
});

describe('the session to count down to', () => {
	it('is the next to start, then none once the race has begun', () => {
		expect(nextSession(austin, at('2026-10-20T00:00:00Z'))?.name).toBe('Practice 1');
		expect(nextSession(austin, at('2026-10-24T22:00:00Z'))?.name).toBe('Race');
		expect(nextSession(austin, at('2026-10-25T20:30:00Z'))).toBeNull();
	});

	it('marks each session done, next or later', () => {
		const now = at('2026-10-24T22:00:00Z');
		expect(austin.sessions.map((s) => sessionPhase(s, austin, now))).toEqual([
			'done',
			'done',
			'next'
		]);
	});

	it('says lights out for a race and names any other session', () => {
		expect(countdownLabel(austin.sessions[2]!)).toBe('Lights out in');
		expect(countdownLabel(austin.sessions[1]!)).toBe('Qualifying starts in');
	});
});
