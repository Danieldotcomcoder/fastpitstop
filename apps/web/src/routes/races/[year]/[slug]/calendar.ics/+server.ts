import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { findRace, weekendCalendar } from '$lib/races/view';
import { SITE_URL } from '$lib/seo';
import { seasonSchedule } from '$lib/server/schedule';

/** "Add to calendar" on a Grand Prix page: the weekend's sessions as an
 * iCalendar file, every time in UTC so the viewer's calendar shows them in
 * their own zone. */
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
	const body = weekendCalendar(race, `${SITE_URL}/races/${year}/${params.slug}`, new Date());
	return new Response(body, {
		headers: {
			'content-type': 'text/calendar; charset=utf-8',
			'content-disposition': `attachment; filename="${year}-${params.slug}.ics"`,
			'cache-control': 'public, max-age=3600'
		}
	});
};
