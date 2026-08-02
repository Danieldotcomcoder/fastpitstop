/** Smooth car motion between position updates. Delay Sync only ever hands a
 * view the position samples already due (never a future one — that would be
 * a spoiler leak), so by the time a car's newest sample is visible the
 * render clock is usually already AT or PAST it, not neatly between two
 * known points. This lerps between the two most recent raw samples and
 * CLAMPS at the newest once the render clock passes it, holding position
 * there until the next sample arrives rather than extrapolating a car off
 * its own track. `stale` flags a hold that has gone on suspiciously long
 * (the feed stalled, the session ended) so a renderer can fade the marker
 * instead of pretending the position is still live. */
import type { PositionSample } from './types';

export interface InterpolatedPosition {
	x: number;
	y: number;
	stale: boolean;
}

export const DEFAULT_STALE_AFTER_MS = 2000;

export function interpolatePosition(
	current: PositionSample | null,
	previous: PositionSample | null,
	renderTsMs: number,
	staleAfterMs: number = DEFAULT_STALE_AFTER_MS
): InterpolatedPosition | null {
	if (current === null) return null;
	const stale = renderTsMs - current.tsMs > staleAfterMs;
	if (previous === null) {
		return { x: current.x, y: current.y, stale };
	}
	const span = current.tsMs - previous.tsMs;
	const t = span <= 0 ? 1 : Math.min(1, Math.max(0, (renderTsMs - previous.tsMs) / span));
	return {
		x: previous.x + (current.x - previous.x) * t,
		y: previous.y + (current.y - previous.y) * t,
		stale
	};
}
