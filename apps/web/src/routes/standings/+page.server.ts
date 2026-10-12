import type { PageServerLoad } from './$types';

/** Rendered on the server for its title and frame; the standings themselves
 * are fetched in the browser, cut off at the last race the viewer watched
 * (`$lib/standings`). */
export const ssr = true;

export const load: PageServerLoad = () => {
	const season = new Date().getUTCFullYear();
	return { season, seasons: [season, season - 1, season - 2] };
};
