/** The playback clock: `wall_now - user_delay`. This one function is the
 * entire spoiler-safety guarantee's arithmetic — everything else
 * (`TimelineBuffer.release`, the controller's `tick`) just compares an
 * envelope's `event_time_utc` against what this returns. Delay is taken in
 * SECONDS (matching `f1_replay`'s own `--delay` and `f1_api`'s
 * `pacing_offset_s` — the same unit the rest of this project uses for a
 * stream offset), wall time in epoch MILLISECONDS (`Date.now()`'s unit,
 * since that's what a browser clock actually gives you). */
export function playbackClockMs(wallNowMs: number, delaySeconds: number): number {
	return wallNowMs - delaySeconds * 1000;
}

export function clamp(value: number, min: number, max: number): number {
	if (min > max) {
		throw new Error(`clamp: min (${min}) is greater than max (${max})`);
	}
	return Math.min(Math.max(value, min), max);
}
