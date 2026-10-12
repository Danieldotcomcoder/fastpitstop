import type { RequestHandler } from './$types';
import { renderSitemap, sitemapPaths } from '$lib/seo';
import { seoCircuits } from '$lib/server/circuits';
import { seoRaces } from '$lib/server/schedule';
import { seoSessions } from '$lib/server/sessions';

/** Every page worth indexing: the content pages, the Grand Prix pages, one
 * replay page per Grand Prix weekend and one page per circuit. */
export const GET: RequestHandler = async () => {
	// This season's Grand Prix pages and last season's: the ones people
	// search for by name.
	const year = new Date().getUTCFullYear();
	const [sessions, circuits, current, previous] = await Promise.all([
		seoSessions(),
		seoCircuits(),
		seoRaces(year),
		seoRaces(year - 1)
	]);
	const body = renderSitemap(sitemapPaths(sessions, circuits, [...current, ...previous]));
	return new Response(body, {
		headers: {
			'content-type': 'application/xml; charset=utf-8',
			'cache-control': 'public, max-age=3600'
		}
	});
};
