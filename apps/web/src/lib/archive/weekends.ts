/** The archive by weekend (ROADMAP W2 D5, the W2 mockup): one card per
 * round, every session it holds as a link in running order, and a strip of
 * the season's calendar. Pure, so it is tested without a browser. Nothing
 * here carries a result: a card names the event, its circuit and the
 * sessions that can be replayed from their start. */
import type { components } from '$lib/api/schema';
import { raceSlug } from '$lib/races/view';
import { SESSION_TYPE_LABELS } from '$lib/seo';

type SessionSummary = components['schemas']['SessionSummaryOut'];
type ScheduleEvent = components['schemas']['ScheduleEventOut'];

/** A weekend's sessions in the order they run, sprint weekends included. */
export const RUNNING_ORDER = [
	'practice_1',
	'practice_2',
	'sprint_qualifying',
	'sprint',
	'practice_3',
	'qualifying',
	'race'
];

export interface WeekendSession {
	type: string;
	label: string;
	href: string;
	live: boolean;
}

export interface Weekend {
	year: number;
	round: number;
	eventName: string;
	circuitName: string | null;
	sessions: WeekendSession[];
	/** Taken in from the live feed (for at least one session). */
	liveRecorded: boolean;
	hasRadio: boolean;
	hasTelemetry: boolean;
}

function rank(type: string): number {
	const i = RUNNING_ORDER.indexOf(type);
	return i < 0 ? RUNNING_ORDER.length : i;
}

/** The replay of one session: the race is its round's page. A session
 * still running opens `/live`. */
export function sessionHref(s: SessionSummary): string {
	if (s.live) return '/live';
	const { year, round, session_type } = s.session_key;
	const path = `/sessions/${year}/${round}`;
	return session_type === 'race' ? path : `${path}?session=${session_type}`;
}

/** One card per round of `year`, newest round first. */
export function weekends(sessions: readonly SessionSummary[], year: number): Weekend[] {
	const byRound = new Map<number, SessionSummary[]>();
	for (const s of sessions) {
		if (s.session_key.year !== year) continue;
		const list = byRound.get(s.session_key.round) ?? [];
		list.push(s);
		byRound.set(s.session_key.round, list);
	}
	return [...byRound.entries()]
		.sort(([a], [b]) => b - a)
		.map(([round, list]) => {
			const sorted = [...list].sort(
				(a, b) => rank(a.session_key.session_type) - rank(b.session_key.session_type)
			);
			const named = sorted.find((s) => s.session_key.session_type === 'race') ?? sorted[0];
			return {
				year,
				round,
				eventName: named?.event_name ?? `Round ${round}`,
				circuitName: sorted.find((s) => s.circuit_name)?.circuit_name ?? null,
				sessions: sorted.map((s) => ({
					type: s.session_key.session_type,
					label: SESSION_TYPE_LABELS[s.session_key.session_type] ?? s.session_key.session_type,
					href: sessionHref(s),
					live: s.live === true
				})),
				liveRecorded: sorted.some((s) => s.live_recorded),
				hasRadio: sorted.some((s) => s.has_radio),
				hasTelemetry: sorted.some((s) => s.has_telemetry)
			};
		});
}

export interface CalendarRound {
	round: number;
	eventName: string;
	circuitId: string | null;
	circuitName: string | null;
	/** Run already, the next to run, or later in the season. */
	state: 'run' | 'next' | 'later';
	/** The catalog holds a session of it, to replay. */
	held: boolean;
	/** Its replay when the catalog holds the round; else its Grand Prix page. */
	href: string;
}

/** The season's calendar as a strip: a round counts as run once its race
 * has started. */
export function calendar(
	events: readonly ScheduleEvent[],
	sessions: readonly SessionSummary[],
	nowMs: number
): CalendarRound[] {
	const rounds = events.filter((e) => e.round > 0 && e.event_format !== 'testing');
	const held = new Set(sessions.map((s) => `${s.session_key.year}/${s.session_key.round}`));
	let nextMarked = false;
	return [...rounds]
		.sort((a, b) => a.round - b.round)
		.map((e) => {
			const last = e.sessions.at(-1);
			const run = last !== undefined && new Date(last.start_utc).getTime() <= nowMs;
			let state: CalendarRound['state'] = run ? 'run' : 'later';
			if (!run && !nextMarked) {
				state = 'next';
				nextMarked = true;
			}
			const key = `${e.season}/${e.round}`;
			const isHeld = held.has(key);
			return {
				round: e.round,
				eventName: e.event_name,
				circuitId: e.circuit_id ?? null,
				circuitName: e.circuit_name ?? null,
				state,
				held: isHeld,
				href: isHeld
					? `/sessions/${e.season}/${e.round}`
					: `/races/${e.season}/${raceSlug(e.event_name)}`
			};
		});
}

/** "17 rounds run, round 19 next". */
export function calendarSummary(rounds: readonly CalendarRound[]): string {
	const run = rounds.filter((r) => r.state === 'run').length;
	const next = rounds.find((r) => r.state === 'next');
	const parts = [`${run} of ${rounds.length} rounds run`];
	if (next) parts.push(`round ${next.round} next`);
	return parts.join(', ');
}
