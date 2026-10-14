import type { PageServerLoad } from './$types';
import { sessionRows } from '$lib/server/sessions';

/** The round's sessions, resolved by the web server and sent inside the
 * page, so a phone opens the race without first downloading the whole
 * catalog (58 KB, the longest step before the desk could paint: ROADMAP W7's
 * load-time target). The list is the one the page's title is already built
 * from (cached briefly), so this costs the server nothing. Empty when the
 * API was unreachable from the server: the page then fetches it itself. */
export const load: PageServerLoad = async ({ params }) => {
	const year = Number(params.year);
	const round = Number(params.round);
	const rows = await sessionRows();
	return {
		weekend: rows.filter((s) => s.session_key.year === year && s.session_key.round === round)
	};
};
