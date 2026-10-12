import type { PageServerLoad } from './$types';
import { circuitList } from '$lib/server/circuits';
import { sessionRows } from '$lib/server/sessions';
import { outlinePath } from '$lib/ui/geometry';
import type { Point } from '$lib/track-map/types';

/** Rendered on the server so the archive's HTML already links every Grand
 * Prix replay: the page search engines crawl from. An empty list (the API
 * unreachable from the server) falls back to the page's own client fetch.
 * Each card shows its circuit's outline, fitted here by circuit name, so
 * the page ships one small path per circuit rather than its points. */
export const ssr = true;

const THUMB = { width: 72, height: 50, stroke: 1.5 } as const;

export const load: PageServerLoad = async () => {
	const [sessions, circuits] = await Promise.all([sessionRows(), circuitList()]);
	const thumbs: Record<string, string> = {};
	for (const circuit of circuits) {
		if (!circuit.outline) continue;
		const points = circuit.outline.points as Point[];
		thumbs[circuit.name] = outlinePath(points, THUMB.width, THUMB.height, THUMB.stroke * 3);
	}
	return { sessions, thumbs, thumbSize: THUMB };
};
