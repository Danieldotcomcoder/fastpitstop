import type { PageServerLoad } from './$types';
import { nextRace } from '$lib/server/schedule';
import { sessionRows } from '$lib/server/sessions';

/** What the page first shows, sent with it: the session list (to find a
 * running session) and the next race (the idle hub). Both are the web
 * server's own short-lived copies, so a phone paints the page without
 * first asking the API (ROADMAP W7's load-time target). The page still
 * polls the API itself from then on. */
export const load: PageServerLoad = async () => {
	const [sessions, next] = await Promise.all([sessionRows(), nextRace()]);
	return { sessions, next };
};
