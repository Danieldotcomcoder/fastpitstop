import type { PageServerLoad } from './$types';
import { circuitSlug } from '$lib/circuit/map';
import { circuitList } from '$lib/server/circuits';
import { outlinePath } from '$lib/ui/geometry';
import type { Point } from '$lib/track-map/types';

/** Rendered on the server: the index search engines crawl every circuit
 * page from. Each outline is fitted here, so the page ships a path rather
 * than every circuit's points. */
export const ssr = true;

const THUMB = { width: 160, height: 110, stroke: 2 } as const;

export const load: PageServerLoad = async () => {
	const circuits = await circuitList();
	return {
		circuits: circuits
			.map((c) => ({
				slug: circuitSlug(c.circuit_id),
				name: c.name,
				country: c.country,
				corners: c.corners,
				lapKm: c.lap_distance_m / 1000,
				pitLossS: c.pit_loss_s,
				measured: c.pit_loss_provenance === 'calibrated',
				thumb: c.outline
					? outlinePath(c.outline.points as Point[], THUMB.width, THUMB.height, THUMB.stroke * 3)
					: null
			}))
			.sort((a, b) => a.country.localeCompare(b.country) || a.name.localeCompare(b.name))
	};
};
