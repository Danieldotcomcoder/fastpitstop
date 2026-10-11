/** What the home page's next-race block says (ROADMAP W3): the session to
 * count down to, the weekend's dates and each session's time at the track.
 * Pure, so it is the same on the server and in the browser; the viewer's
 * own time zone is added by the component once it runs in the browser. */
import type { components } from '$lib/api/schema';

export type ScheduleEvent = components['schemas']['ScheduleEventOut'];
export type ScheduleSession = components['schemas']['ScheduleSessionOut'];

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** The wall-clock fields of an ISO 8601 time, as written: for a circuit-local
 * time ("2026-10-25T15:00:00-05:00") that is the time at the track, whatever
 * zone this code runs in. */
function wallClock(
	iso: string
): { year: number; month: number; day: number; hour: number; minute: number } | null {
	const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
	if (!match) return null;
	const [, y, mo, d, h, mi] = match.map(Number) as [number, number, number, number, number, number];
	return { year: y, month: mo, day: d, hour: h, minute: mi };
}

function weekday(year: number, month: number, day: number): string {
	return DAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()] ?? '';
}

/** "Sun 15:00": a session's start at the track. Falls back to UTC (marked)
 * when the schedule has no local time. */
export function trackTime(session: ScheduleSession): string {
	const local = session.start_local ? wallClock(session.start_local) : null;
	const clock = local ?? wallClock(session.start_utc);
	if (!clock) return '—';
	const time = `${String(clock.hour).padStart(2, '0')}:${String(clock.minute).padStart(2, '0')}`;
	return `${weekday(clock.year, clock.month, clock.day)} ${time}${local ? '' : ' UTC'}`;
}

/** "23–25 Oct" (or "30 Oct – 1 Nov"): the weekend at the track. */
export function weekendDates(event: ScheduleEvent): string {
	const first = event.sessions[0];
	const last = event.sessions.at(-1);
	if (!first || !last) return '';
	const a = wallClock(first.start_local ?? first.start_utc);
	const b = wallClock(last.start_local ?? last.start_utc);
	if (!a || !b) return '';
	const monthA = MONTHS[a.month - 1] ?? '';
	const monthB = MONTHS[b.month - 1] ?? '';
	if (a.month === b.month)
		return a.day === b.day ? `${a.day} ${monthB}` : `${a.day}–${b.day} ${monthB}`;
	return `${a.day} ${monthA} – ${b.day} ${monthB}`;
}

/** The next session to start, or null once the weekend's last has begun. */
export function nextSession(event: ScheduleEvent, nowMs: number): ScheduleSession | null {
	return event.sessions.find((s) => new Date(s.start_utc).getTime() > nowMs) ?? null;
}

export type SessionPhase = 'done' | 'next' | 'later';

/** Started already, the next to start, or later in the weekend. */
export function sessionPhase(
	session: ScheduleSession,
	event: ScheduleEvent,
	nowMs: number
): SessionPhase {
	if (new Date(session.start_utc).getTime() <= nowMs) return 'done';
	return nextSession(event, nowMs) === session ? 'next' : 'later';
}

/** "Lights out in" for a race or sprint, "Qualifying starts in" otherwise. */
export function countdownLabel(session: ScheduleSession): string {
	if (session.session_type === 'race' || session.session_type === 'sprint') return 'Lights out in';
	return `${session.name} starts in`;
}
