/** Minimal ISO 8601 duration parser for the subset pydantic actually emits
 * for our `timedelta` fields (hours/minutes/seconds, optionally fractional,
 * optionally negative) — e.g. `"PT1.234S"`, `"PT1M2.5S"`, `"-PT0.5S"`.
 * Domain events carry raw `timedelta` fields (`GapUpdated.gap_to_leader`,
 * `LapCompleted.lap_time`) that `f1_api.convert` normally turns into a plain
 * float for REST responses, but the WebSocket stream serializes the domain
 * event verbatim (`DOMAIN_EVENT_ADAPTER.dump_python(event, mode="json")`) —
 * this is the one place that needs to undo it. */
const ISO_DURATION =
	/^(-)?P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$/;

export function parseIsoDurationSeconds(value: unknown): number | null {
	if (typeof value !== 'string') return null;
	const match = ISO_DURATION.exec(value.trim());
	if (!match) return null;
	const [, sign, days, hours, minutes, seconds] = match;
	if (!days && !hours && !minutes && !seconds) return null;
	const total =
		Number(days ?? 0) * 86400 +
		Number(hours ?? 0) * 3600 +
		Number(minutes ?? 0) * 60 +
		Number(seconds ?? 0);
	return sign ? -total : total;
}
