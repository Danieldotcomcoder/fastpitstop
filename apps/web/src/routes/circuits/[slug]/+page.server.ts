import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { circuitIdFromSlug, circuitMap } from '$lib/circuit/map';
import { pitLossView, wearRows, wearSeasons } from '$lib/circuit/profile';
import { circuitList, circuitProfile, type CircuitProfile } from '$lib/server/circuits';
import { toSeoCircuit } from '$lib/seo';
import type { Point } from '$lib/track-map/types';

/** A circuit page (ROADMAP S2), rendered on the server: it is a page people
 * search for. The map is drawn here too, so the page ships SVG paths
 * rather than the outline's points. */
export const ssr = true;

const MAP = { width: 640, height: 440 } as const;

export const load: PageServerLoad = async ({ params }) => {
	if (!/^[a-z0-9-]+$/.test(params.slug)) error(404, 'No such circuit');
	let profile: CircuitProfile | null;
	try {
		profile = await circuitProfile(circuitIdFromSlug(params.slug));
	} catch {
		error(503, 'The circuit data is unavailable right now. Try again in a minute.');
	}
	if (profile === null) error(404, 'No such circuit');
	const all = await circuitList();
	const map = profile.outline
		? circuitMap(
				profile.outline.points as Point[],
				profile.lap_distance_m,
				profile.corners,
				profile.drs_zones,
				MAP.width,
				MAP.height
			)
		: null;
	return {
		seo: toSeoCircuit(profile),
		name: profile.name,
		country: profile.country,
		lapKm: profile.lap_distance_m / 1000,
		corners: profile.corners.length,
		drsZones: profile.drs_zones.length,
		map,
		mapSize: MAP,
		pitLoss: pitLossView(profile, all.length > 0 ? all : [profile]),
		wear: wearRows(profile.tyre_wear),
		wearSeasons: wearSeasons(profile.tyre_wear),
		races: profile.races.map((r) => ({ season: r.season, round: r.round, eventName: r.event_name }))
	};
};
