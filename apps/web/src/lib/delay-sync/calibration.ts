/** Auto-calibration: "tap when the lights go out" (or tap on any incident
 * whose `event_time_utc` you know). The user's broadcast delay is exactly
 * how far their tap's wall-clock time trails the real event's UTC time —
 * there is no other signal needed. */

/** `eventTimeUtc` accepts either an ISO string (as it arrives on the wire)
 * or an epoch-ms number (for tests that want to skip the string round-trip).
 * Returns the offset in SECONDS, matching the rest of this project's delay
 * unit. A tap that lands before the event's own timestamp (clock skew, a
 * mis-tap) would produce a negative offset; callers clamp it (Delay Sync's
 * controller clamps to the slider's own [min, max]) since a negative delay
 * has no meaningful playback-clock interpretation here. */
export function delaySecondsFromTap(tapWallMs: number, eventTimeUtc: string | number): number {
	const eventMs = typeof eventTimeUtc === 'string' ? Date.parse(eventTimeUtc) : eventTimeUtc;
	if (Number.isNaN(eventMs)) {
		throw new Error(`delaySecondsFromTap: unparseable event_time_utc: ${eventTimeUtc}`);
	}
	return (tapWallMs - eventMs) / 1000;
}
