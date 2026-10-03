import type { RequestHandler } from './$types';
import { renderSitemap, sitemapPaths } from '$lib/seo';
import { seoSessions } from '$lib/server/sessions';

/** Every page worth indexing: the content pages and one replay page per
 * Grand Prix weekend. */
export const GET: RequestHandler = async () => {
	const body = renderSitemap(sitemapPaths(await seoSessions()));
	return new Response(body, {
		headers: {
			'content-type': 'application/xml; charset=utf-8',
			'cache-control': 'public, max-age=3600'
		}
	});
};
