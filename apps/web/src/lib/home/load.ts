/** The next round and its circuit outline, fetched from the browser (for
 * `/live`, which renders client-side; the home page reads the same thing on
 * the server through `$lib/server/schedule`). */
import { api } from '$lib/api/client';
import { outlineFromApi } from '$lib/track-map';
import type { Point } from '$lib/track-map/types';
import type { ScheduleEvent } from './next-race';

export interface NextRaceView {
	event: ScheduleEvent;
	outline: Point[] | null;
}

export async function loadNextRace(signal?: AbortSignal): Promise<NextRaceView | null> {
	const { data: event } = await api.GET('/schedule/next', { signal });
	if (!event) return null;
	let outline: Point[] | null = null;
	if (event.circuit_id) {
		try {
			const { data } = await api.GET('/circuits/{circuit_id}', {
				params: { path: { circuit_id: event.circuit_id } },
				signal
			});
			const parsed = outlineFromApi(data?.outline);
			outline = parsed && parsed.points.length > 1 ? parsed.points : null;
		} catch {
			// The block reads fine without the drawing.
		}
	}
	return { event, outline };
}
