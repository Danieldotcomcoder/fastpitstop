/** Text formatting for the v2 components: signed deltas, places gained,
 * countdowns and session times in the viewer's own time zone. */

const MINUS = '−';

/** A signed value with a true minus sign: `+0.4`, `−1.2`. Zero has no sign.
 * `null` (unknown) is an em dash, never a made-up zero. */
export function formatDelta(value: number | null, digits = 1, unit = ''): string {
	if (value === null || !Number.isFinite(value)) return '—';
	const fixed = Math.abs(value).toFixed(digits);
	if (Number(fixed) === 0) return `${fixed}${unit}`;
	return `${value > 0 ? '+' : MINUS}${fixed}${unit}`;
}

export type PositionDelta =
	{ kind: 'gain' | 'loss'; places: number } | { kind: 'same' } | { kind: 'unknown' };

/** Places gained (or lost) since `start`; a lower position number is a gain. */
export function positionDelta(start: number | null, now: number | null): PositionDelta {
	if (start === null || now === null) return { kind: 'unknown' };
	if (now === start) return { kind: 'same' };
	return now < start
		? { kind: 'gain', places: start - now }
		: { kind: 'loss', places: now - start };
}

export interface CountdownParts {
	days: number;
	hours: number;
	minutes: number;
	seconds: number;
	/** The target has passed; every field is 0. */
	past: boolean;
}

export function countdownParts(targetMs: number, nowMs: number): CountdownParts {
	const left = Math.floor((targetMs - nowMs) / 1000);
	if (!(left > 0)) return { days: 0, hours: 0, minutes: 0, seconds: 0, past: true };
	return {
		days: Math.floor(left / 86_400),
		hours: Math.floor((left % 86_400) / 3_600),
		minutes: Math.floor((left % 3_600) / 60),
		seconds: left % 60,
		past: false
	};
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** `3d 04h 12m` while a day or more away, then `04:12:09`. */
export function formatCountdown(parts: CountdownParts): string {
	if (parts.days > 0) return `${parts.days}d ${pad2(parts.hours)}h ${pad2(parts.minutes)}m`;
	return `${pad2(parts.hours)}:${pad2(parts.minutes)}:${pad2(parts.seconds)}`;
}

/** A session start in the viewer's time zone, e.g. `Sun 14:00`. `timeZone`
 * and `locale` are for tests; the page passes neither and gets the
 * viewer's own. */
export function sessionTime(
	isoUtc: string,
	options: { timeZone?: string; locale?: string } = {}
): string {
	const date = new Date(isoUtc);
	if (Number.isNaN(date.getTime())) return '—';
	const fmt = new Intl.DateTimeFormat(options.locale ?? 'en-GB', {
		weekday: 'short',
		hour: '2-digit',
		minute: '2-digit',
		hourCycle: 'h23',
		timeZone: options.timeZone
	});
	return fmt.format(date).replace(',', '');
}
