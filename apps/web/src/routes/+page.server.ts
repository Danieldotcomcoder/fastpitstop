import type { PageServerLoad } from './$types';
import { nextRace } from '$lib/server/schedule';
import { seoSessions } from '$lib/server/sessions';

/** The landing page is rendered on the server: it is the page search
 * engines and link previews read first, and it must carry real content
 * rather than the empty shell the dashboards start as. */
export const ssr = true;

export const load: PageServerLoad = async () => {
	const [sessions, next] = await Promise.all([seoSessions(), nextRace()]);
	return { sessions, next };
};
