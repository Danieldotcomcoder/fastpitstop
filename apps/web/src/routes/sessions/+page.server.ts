import type { PageServerLoad } from './$types';
import { circuitList } from '$lib/server/circuits';
import { seasonSchedule, type ScheduleEvent } from '$lib/server/schedule';
import { sessionRows } from '$lib/server/sessions';
import { outlinePath } from '$lib/ui/geometry';
import type { Point } from '$lib/track-map/types';

/** Rendered on the server so the archive's HTML already links every Grand
 * Prix replay: the page search engines crawl from. An empty list (the API
 * unreachable from the server) falls back to the page's own client fetch.
 * Each weekend shows its circuit's outline, fitted here by circuit name,
 * so the page ships one small path per circuit rather than its points;
 * the season strip (ROADMAP W2) draws each calendar round the same way,
 * found by circuit id. */
export const ssr = true;

const THUMB = { width: 72, height: 50, stroke: 1.5 } as const;
const CARD = { width: 132, height: 92, stroke: 1.75 } as const;

export const load: PageServerLoad = async () => {
	const [sessions, circuits] = await Promise.all([sessionRows(), circuitList()]);
	const cards: Record<string, string> = {};
	const byId: Record<string, string> = {};
	for (const circuit of circuits) {
		if (!circuit.outline) continue;
		const points = circuit.outline.points as Point[];
		cards[circuit.name] = outlinePath(points, CARD.width, CARD.height, CARD.stroke * 3);
		byId[circuit.circuit_id] = outlinePath(points, THUMB.width, THUMB.height, THUMB.stroke * 3);
	}
	// Each season the archive holds, for its calendar strip. A season the
	// API can't answer for simply has no strip.
	const years = [...new Set(sessions.map((s) => s.session_key.year))];
	const schedules: Record<number, ScheduleEvent[]> = {};
	await Promise.all(
		years.map(async (year) => {
			schedules[year] = await seasonSchedule(year).catch(() => []);
		})
	);
	return {
		sessions,
		cards,
		thumbsById: byId,
		schedules,
		thumbSize: THUMB,
		cardSize: CARD
	};
};
