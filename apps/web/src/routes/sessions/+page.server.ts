import type { PageServerLoad } from './$types';
import { sessionRows } from '$lib/server/sessions';

/** Rendered on the server so the archive's HTML already links every Grand
 * Prix replay: the page search engines crawl from. An empty list (the API
 * unreachable from the server) falls back to the page's own client fetch. */
export const ssr = true;

export const load: PageServerLoad = async () => ({ sessions: await sessionRows() });
