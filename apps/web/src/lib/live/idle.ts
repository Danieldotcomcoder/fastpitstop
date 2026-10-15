/** Which screen `/live` shows while no session is running (ROADMAP W2 D5,
 * W3's table): an off week, the run-up to a session, or just after one.
 * Pure over the calendar's next round, the session list and the clock, so
 * every state is tested without a browser.
 *
 * Nothing here names a result: a finished session is offered only as a
 * replay from its start. */
import type { ScheduleEvent, ScheduleSession } from '$lib/home/next-race';

/** A session the catalog holds, as this module needs it. */
export interface HeldSession {
	year: number;
	round: number;
	sessionType: string;
	live: boolean;
}

/** One of this weekend's sessions that has already started. */
export interface EarlierSession {
	session: ScheduleSession;
	/** Its replay, when the catalog holds it; null while it is being filed. */
	href: string | null;
}

export type IdleState =
	| { kind: 'off'; next: ScheduleSession | null }
	| { kind: 'pre'; next: ScheduleSession; earlier: EarlierSession[] }
	| {
			kind: 'post';
			finished: ScheduleSession;
			href: string | null;
			next: ScheduleSession | null;
			earlier: EarlierSession[];
	  };

/** How close the next session must be for `/live` to count down to it
 * rather than show the week ahead. */
export const PRE_SESSION_MS = 24 * 3600_000;
/** Inside this of the next session, the screen counts down to it even if
 * the last one has only just finished. */
export const COUNTDOWN_WINDOW_MS = 60 * 60_000;

/** The replay page of one session of a round: the race is the round's own
 * page, any other session picks itself with `?session=`. */
export function replayHref(year: number, round: number, sessionType: string): string {
	const path = `/sessions/${year}/${round}`;
	return sessionType === 'race' ? path : `${path}?session=${sessionType}`;
}

function startMs(s: ScheduleSession): number {
	return new Date(s.start_utc).getTime();
}

export function idleState(
	event: ScheduleEvent,
	held: readonly HeldSession[],
	nowMs: number
): IdleState {
	const started = event.sessions.filter((s) => startMs(s) <= nowMs);
	const next = event.sessions.find((s) => startMs(s) > nowMs) ?? null;
	const earlier = started.map((session) => {
		const type = session.session_type;
		const replay =
			type !== null &&
			held.some(
				(h) =>
					h.year === event.season && h.round === event.round && h.sessionType === type && !h.live
			);
		return {
			session,
			href: replay && type !== null ? replayHref(event.season, event.round, type) : null
		};
	});

	if (started.length === 0) {
		if (next !== null && startMs(next) - nowMs <= PRE_SESSION_MS) {
			return { kind: 'pre', next, earlier: [] };
		}
		return { kind: 'off', next };
	}

	const latest = earlier[earlier.length - 1] as EarlierSession;
	if (next !== null && (latest.href === null || startMs(next) - nowMs <= COUNTDOWN_WINDOW_MS)) {
		// Between sessions with nothing new to watch yet, or the next one
		// close: count down to it.
		return { kind: 'pre', next, earlier };
	}
	return {
		kind: 'post',
		finished: latest.session,
		href: latest.href,
		next,
		earlier: earlier.slice(0, -1)
	};
}
