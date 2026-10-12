import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';
import { circuitSlug } from '$lib/circuit/map';
import { pitLossView } from '$lib/circuit/profile';
import { findRace, toSeoRace, type ScheduleEvent } from '$lib/races/view';
import { circuitList, circuitProfile, type CircuitProfile } from '$lib/server/circuits';
import { seasonSchedule } from '$lib/server/schedule';
import { seoSessions } from '$lib/server/sessions';
import type { Point } from '$lib/track-map/types';
import { outlinePath } from '$lib/ui/geometry';

/** A Grand Prix page (ROADMAP S3), rendered on the server: the page people
 * land on searching "<race> start time". Holds no result of this race;
 * the strategy shown is the previous race at the circuit. */
export const ssr = true;

const MAP = { width: 520, height: 360 } as const;

export const load: PageServerLoad = async ({ params }) => {
	const year = Number(params.year);
	if (!/^\d{4}$/.test(params.year) || year < 2018 || year > 2100) error(404, 'No such race');
	let found: ScheduleEvent | null;
	try {
		found = findRace(await seasonSchedule(year), params.slug);
	} catch {
		error(503, 'The calendar is unavailable right now. Try again in a minute.');
	}
	if (found === null) error(404, 'No such race');
	const race = found;

	let profile: CircuitProfile | null = null;
	if (race.circuit_id) {
		profile = await circuitProfile(race.circuit_id).catch(() => null);
	}
	const [all, sessions] = await Promise.all([circuitList(), seoSessions()]);
	const measured = all.filter((c) => c.pit_loss_provenance === 'calibrated');
	// The latest race at this circuit before this season: how it was run is
	// the useful prior, and it can't spoil this weekend.
	const previous = profile?.races.find((r) => r.season < year && r.strategy !== null) ?? null;
	const ours = sessions.filter((s) => s.year === year && s.round === race.round);

	return {
		race,
		seo: toSeoRace(race),
		circuit: profile
			? {
					slug: circuitSlug(profile.circuit_id),
					name: profile.name,
					lapKm: profile.lap_distance_m / 1000,
					corners: profile.corners.length,
					drsZones: profile.drs_zones.length,
					pitLoss: pitLossView(profile, all.length > 0 ? all : [profile]),
					range:
						measured.length > 1
							? {
									low: Math.min(...measured.map((c) => c.pit_loss_s)),
									high: Math.max(...measured.map((c) => c.pit_loss_s))
								}
							: null,
					outline: profile.outline
						? outlinePath(profile.outline.points as Point[], MAP.width, MAP.height, 6)
						: null
				}
			: null,
		mapSize: MAP,
		previous: previous ? { season: previous.season, strategy: previous.strategy! } : null,
		live: ours.some((s) => s.live),
		replay: ours.some((s) => s.sessionType === 'race' && !s.live)
	};
};
