import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { findRace, toSeoRace } from '$lib/races/view';
import { circuitProfile } from '$lib/server/circuits';
import { renderShareCard } from '$lib/server/og/render';
import { seasonSchedule } from '$lib/server/schedule';
import type { Point } from '$lib/track-map/types';

/** A Grand Prix page's share image (ROADMAP S6): its circuit, name, round,
 * venue and dates as a 1200x630 PNG, never a result. Rendered on first
 * request and cached, since the calendar it is drawn from lives in the API
 * and a build has no API to ask. A circuit the API can't describe right
 * now still gets its card, without the drawing. */
export const GET: RequestHandler = async ({ params }) => {
	const year = Number(params.year);
	if (!/^\d{4}$/.test(params.year) || year < 2018 || year > 2100) error(404, 'No such race');
	let race;
	try {
		race = findRace(await seasonSchedule(year), params.slug);
	} catch {
		error(503, 'The calendar is unavailable right now.');
	}
	if (race === null) error(404, 'No such race');
	const profile = race.circuit_id ? await circuitProfile(race.circuit_id).catch(() => null) : null;
	const seo = toSeoRace(race);
	const png = renderShareCard({
		season: year,
		round: race.round,
		eventName: race.event_name,
		venue: seo.venue,
		dates: seo.dates,
		outline: (profile?.outline?.points as Point[] | undefined) ?? null
	});
	return new Response(new Uint8Array(png), {
		headers: {
			'content-type': 'image/png',
			'cache-control': 'public, max-age=86400'
		}
	});
};
