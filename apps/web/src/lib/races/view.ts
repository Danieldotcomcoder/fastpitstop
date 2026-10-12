/** A Grand Prix page (ROADMAP S3): its address, how the last race at the
 * circuit was run, and the weekend as a calendar file. Pure, so the server
 * page, the sitemap and the calendar download all agree. */
import type { components } from '$lib/api/schema';
import { weekendDates } from '$lib/home/next-race';
import type { SeoRace } from '$lib/seo';

export type ScheduleEvent = components['schemas']['ScheduleEventOut'];
export type RaceStrategy = components['schemas']['RaceStrategyOut'];

/** "São Paulo Grand Prix" -> "sao-paulo": the event's own name, so the
 * address reads the way people search for the race. */
export function raceSlug(eventName: string): string {
	return eventName
		.normalize('NFKD')
		.replace(/[\u0300-\u036f]/g, '')
		.toLowerCase()
		.replace(/\bgrand prix\b/g, '')
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');
}

/** The rounds that get a page: Grand Prix weekends, not pre-season testing. */
export function grandsPrix(events: readonly ScheduleEvent[]): ScheduleEvent[] {
	return events.filter((e) => e.round > 0 && e.event_format !== 'testing');
}

export function findRace(events: readonly ScheduleEvent[], slug: string): ScheduleEvent | null {
	return grandsPrix(events).find((e) => raceSlug(e.event_name) === slug) ?? null;
}

const COMPOUND: Record<string, string> = {
	soft: 'Soft',
	medium: 'Medium',
	hard: 'Hard',
	intermediate: 'Intermediate',
	wet: 'Wet'
};

/** "Medium → Hard". */
export function planLabel(compounds: readonly string[]): string {
	return compounds.map((c) => COMPOUND[c] ?? c).join(' → ');
}

const TIMES = ['without stopping', 'once', 'twice', 'three times', 'four times'];

/** "Of the 19 finishers, 15 stopped once and 4 twice." Counts only, never
 * who: the page names no result. */
export function strategySentence(strategy: RaceStrategy): string {
	const parts = Object.entries(strategy.stops)
		.map(([stops, cars]) => [Number(stops), cars] as const)
		.filter(([, cars]) => cars > 0)
		.sort((a, b) => a[0] - b[0])
		.map(([stops, cars], i) => {
			const how = TIMES[stops] ?? `${stops} times`;
			const verb = stops === 0 ? 'ran' : 'stopped';
			return i === 0 ? `${cars} ${verb} ${how}` : `${cars} ${how}`;
		});
	const list =
		parts.length <= 1 ? (parts[0] ?? '') : `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}`;
	return `Of the ${strategy.finishers} finishers, ${list}.`;
}

/** How long a calendar entry blocks out for each session. The schedule
 * gives start times only; these are the sessions' nominal lengths. */
const NOMINAL_MINUTES: Record<string, number> = {
	practice_1: 60,
	practice_2: 60,
	practice_3: 60,
	qualifying: 60,
	sprint_qualifying: 45,
	sprint: 60,
	race: 120
};

/** When a session's calendar entry ends: its start plus its nominal length. */
export function sessionEndUtc(session: ScheduleEvent['sessions'][number]): string {
	const minutes = NOMINAL_MINUTES[session.session_type ?? ''] ?? 60;
	return new Date(new Date(session.start_utc).getTime() + minutes * 60_000).toISOString();
}

export function toSeoRace(event: ScheduleEvent): SeoRace {
	return {
		year: event.season,
		round: event.round,
		slug: raceSlug(event.event_name),
		eventName: event.event_name,
		venue: event.circuit_name ?? event.location,
		location: event.location,
		country: event.country,
		dates: weekendDates(event),
		sessions: event.sessions.map((s) => ({
			name: s.name,
			startUtc: new Date(s.start_utc).toISOString(),
			endUtc: sessionEndUtc(s)
		}))
	};
}

const icsTime = (iso: string) =>
	new Date(iso)
		.toISOString()
		.replace(/[-:]/g, '')
		.replace(/\.\d{3}/, '');

/** RFC 5545 text: escape what the format reserves, fold at 75 octets. */
function icsText(value: string): string {
	return value
		.replace(/\\/g, '\\\\')
		.replace(/;/g, '\\;')
		.replace(/,/g, '\\,')
		.replace(/\n/g, '\\n');
}

function fold(line: string): string {
	const bytes = new TextEncoder().encode(line);
	if (bytes.length <= 75) return line;
	const out: string[] = [];
	let current = '';
	let size = 0;
	for (const char of line) {
		const width = new TextEncoder().encode(char).length;
		if (size + width > (out.length === 0 ? 75 : 74)) {
			out.push(current);
			current = '';
			size = 0;
		}
		current += char;
		size += width;
	}
	out.push(current);
	return out.join('\r\n ');
}

/** The weekend's sessions as an iCalendar file, every time in UTC so the
 * viewer's calendar shows them in their own zone. `stamp` is the file's
 * creation time (DTSTAMP), passed in to keep this pure. */
export function weekendCalendar(event: ScheduleEvent, pageUrl: string, stamp: Date): string {
	const where = [event.circuit_name, event.location, event.country]
		.filter((p, i, all): p is string => !!p && all.indexOf(p) === i)
		.join(', ');
	const lines = [
		'BEGIN:VCALENDAR',
		'VERSION:2.0',
		'PRODID:-//FastPitStop//Race weekend//EN',
		'CALSCALE:GREGORIAN',
		'METHOD:PUBLISH',
		`X-WR-CALNAME:${icsText(`${event.season} ${event.event_name}`)}`
	];
	for (const session of event.sessions) {
		const start = new Date(session.start_utc);
		const end = new Date(sessionEndUtc(session));
		lines.push(
			'BEGIN:VEVENT',
			`UID:${event.season}-${event.round}-${raceSlug(session.name)}@fastpitstop.online`,
			`DTSTAMP:${icsTime(stamp.toISOString())}`,
			`DTSTART:${icsTime(start.toISOString())}`,
			`DTEND:${icsTime(end.toISOString())}`,
			`SUMMARY:${icsText(`${event.event_name}: ${session.name}`)}`,
			`LOCATION:${icsText(where)}`,
			`URL:${pageUrl}`,
			`DESCRIPTION:${icsText(`Follow it spoiler-free on FastPitStop (unofficial): ${pageUrl}`)}`,
			'END:VEVENT'
		);
	}
	lines.push('END:VCALENDAR');
	return lines.map(fold).join('\r\n') + '\r\n';
}
