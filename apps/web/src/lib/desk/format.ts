/** Number and label formatting for the race desk, matching the supplied
 * design's own formatters (pitwall-race-dashboard.html) so the rendered
 * figures read identically: lap times as m:ss.sss, signed deltas with a
 * true minus sign, the race clock as h:mm:ss. */

/** A lap time in seconds as `1:32.418`; an em dash when unknown. */
export function fmtLap(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
	const minutes = Math.floor(seconds / 60);
	const rest = seconds - minutes * 60;
	return `${minutes}:${rest < 10 ? '0' : ''}${rest.toFixed(3)}`;
}

/** `+0.42` / `−0.42` / `±0.00`, with a real minus sign (U+2212). */
export function sgn(value: number, digits = 2): string {
	const sign = value > 0 ? '+' : value < 0 ? '−' : '±';
	return sign + Math.abs(value).toFixed(digits);
}

/** Seconds as `h:mm:ss`, never negative. */
export function fmtClock(seconds: number): string {
	const t = Math.max(0, seconds);
	const h = Math.floor(t / 3600);
	const m = Math.floor((t % 3600) / 60);
	const s = Math.floor(t % 60);
	return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

const COMPOUND_LETTERS: Record<string, string> = {
	soft: 'S',
	medium: 'M',
	hard: 'H',
	intermediate: 'I',
	wet: 'W'
};

/** The single letter the tyre disc shows; `?` for an unknown compound. */
export function compoundLetter(compound: string | null | undefined): string {
	if (!compound) return '?';
	return COMPOUND_LETTERS[compound.toLowerCase()] ?? '?';
}

/** "Medium", from the feed's lower-case `medium`. */
export function compoundName(compound: string | null | undefined): string {
	if (!compound) return 'Unknown';
	return compound.charAt(0).toUpperCase() + compound.slice(1).toLowerCase();
}

/** "Norris" from "Lando Norris", "Lando NORRIS" or "NORRIS". */
export function surname(fullName: string | null | undefined): string | null {
	if (!fullName) return null;
	const parts = fullName.trim().split(/\s+/);
	const capitals = parts.filter((part) => part.length > 1 && part === part.toUpperCase());
	const last = capitals.length > 0 ? capitals.join(' ') : (parts[parts.length - 1] ?? '');
	return last
		.toLowerCase()
		.split(' ')
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/** `1 lap` / `3 laps`. */
export function laps(count: number): string {
	return `${count} lap${count === 1 ? '' : 's'}`;
}
