import { describe, expect, it } from 'vitest';
import {
	findRace,
	planLabel,
	raceSlug,
	strategySentence,
	weekendCalendar,
	type ScheduleEvent
} from './view';

const event = (round: number, name: string, format = 'conventional'): ScheduleEvent => ({
	season: 2026,
	round,
	event_name: name,
	location: 'Austin',
	country: 'United States',
	event_format: format,
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
			name: 'Race',
			session_type: 'race',
			start_utc: '2026-10-25T20:00:00Z',
			start_local: '2026-10-25T15:00:00-05:00'
		}
	]
});

describe('raceSlug', () => {
	it('reads the way the race is searched for', () => {
		expect(raceSlug('Singapore Grand Prix')).toBe('singapore');
		expect(raceSlug('United States Grand Prix')).toBe('united-states');
		expect(raceSlug('São Paulo Grand Prix')).toBe('sao-paulo');
		expect(raceSlug('Mexico City Grand Prix')).toBe('mexico-city');
	});

	it('finds a Grand Prix by its slug, never a test', () => {
		const events = [
			event(0, 'Pre-Season Testing', 'testing'),
			event(18, 'United States Grand Prix')
		];
		expect(findRace(events, 'united-states')?.round).toBe(18);
		expect(findRace(events, 'pre-season-testing')).toBeNull();
		expect(findRace(events, 'nowhere')).toBeNull();
	});
});

describe('strategy', () => {
	it('says how the field stopped, counts only', () => {
		expect(strategySentence({ finishers: 19, stops: { '1': 15, '2': 4 }, plans: [] })).toBe(
			'Of the 19 finishers, 15 stopped once and 4 twice.'
		);
		expect(strategySentence({ finishers: 20, stops: { '2': 3, '0': 1, '1': 16 }, plans: [] })).toBe(
			'Of the 20 finishers, 1 ran without stopping, 16 once and 3 twice.'
		);
	});

	it('names a plan by its compounds', () => {
		expect(planLabel(['medium', 'hard'])).toBe('Medium → Hard');
	});
});

describe('weekendCalendar', () => {
	const ics = weekendCalendar(
		event(18, 'United States Grand Prix'),
		'https://fastpitstop.online/races/2026/united-states',
		new Date('2026-10-11T12:00:00Z')
	);

	it('has one entry per session, in UTC, with a nominal length', () => {
		expect(ics.match(/BEGIN:VEVENT/g)).toHaveLength(2);
		expect(ics).toContain('DTSTART:20261025T200000Z\r\nDTEND:20261025T220000Z');
		expect(ics).toContain('DTSTART:20261023T173000Z\r\nDTEND:20261023T183000Z');
		expect(ics).toContain('SUMMARY:United States Grand Prix: Race');
		expect(ics).toContain('LOCATION:Circuit of the Americas\\, Austin\\, United States');
	});

	it('is valid iCalendar: CRLF lines, none longer than 75 octets', () => {
		expect(ics.startsWith('BEGIN:VCALENDAR\r\n')).toBe(true);
		expect(ics.endsWith('END:VCALENDAR\r\n')).toBe(true);
		for (const line of ics.split('\r\n')) {
			expect(new TextEncoder().encode(line).length).toBeLessThanOrEqual(75);
		}
	});
});
