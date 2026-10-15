import { describe, expect, it } from 'vitest';
import type { ScheduleEvent } from '$lib/home/next-race';
import { idleState, replayHref, type HeldSession } from './idle';

const H = 3600_000;
const T0 = Date.UTC(2026, 9, 23, 18, 0); // Practice 1

function event(): ScheduleEvent {
	const s = (name: string, type: string, at: number) => ({
		name,
		session_type: type,
		start_utc: new Date(at).toISOString(),
		start_local: null
	});
	return {
		season: 2026,
		round: 19,
		event_name: 'United States Grand Prix',
		sessions: [
			s('Practice 1', 'practice_1', T0),
			s('Sprint Qualifying', 'sprint_qualifying', T0 + 4 * H),
			s('Sprint', 'sprint', T0 + 20 * H),
			s('Qualifying', 'qualifying', T0 + 24 * H),
			s('Race', 'race', T0 + 44 * H)
		]
	} as unknown as ScheduleEvent;
}
const held = (type: string, live = false): HeldSession => ({
	year: 2026,
	round: 19,
	sessionType: type,
	live
});

describe('idleState', () => {
	it('is an off week until a day before the first session', () => {
		expect(idleState(event(), [], T0 - 3 * 24 * H).kind).toBe('off');
		const pre = idleState(event(), [], T0 - 2 * H);
		expect(pre.kind).toBe('pre');
		expect(pre.kind === 'pre' && pre.next.name).toBe('Practice 1');
	});

	it('offers a finished session from its start once the catalog holds it', () => {
		const state = idleState(event(), [held('practice_1')], T0 + 2 * H);
		expect(state).toMatchObject({ kind: 'post', href: '/sessions/2026/19?session=practice_1' });
		expect(state.kind === 'post' && state.next?.name).toBe('Sprint Qualifying');
	});

	it('counts down instead while the session is not filed yet or the next is close', () => {
		expect(idleState(event(), [], T0 + 2 * H).kind).toBe('pre');
		expect(idleState(event(), [held('practice_1')], T0 + 3.5 * H).kind).toBe('pre');
		// Still being ingested live: not a replay yet.
		expect(idleState(event(), [held('practice_1', true)], T0 + 2 * H).kind).toBe('pre');
	});

	it('lists the earlier sessions with their replays, never other rounds', () => {
		const state = idleState(
			event(),
			[held('practice_1'), held('sprint_qualifying'), { ...held('sprint'), round: 18 }],
			T0 + 22 * H
		);
		expect(state.kind).toBe('pre');
		if (state.kind !== 'pre') return;
		expect(state.earlier.map((e) => [e.session.name, e.href])).toEqual([
			['Practice 1', '/sessions/2026/19?session=practice_1'],
			['Sprint Qualifying', '/sessions/2026/19?session=sprint_qualifying'],
			['Sprint', null]
		]);
	});

	it('after the race, offers the race from the start', () => {
		const state = idleState(event(), [held('race')], T0 + 47 * H);
		expect(state).toMatchObject({ kind: 'post', href: '/sessions/2026/19', next: null });
	});

	it('links the race to its round page', () => {
		expect(replayHref(2026, 19, 'race')).toBe('/sessions/2026/19');
	});
});
