/** Writes each page's search and link-preview tags (`$lib/seo`) into its
 * HTML on the server, so a crawler or a chat app's link preview sees a real
 * title, description and card even for the dashboards that render in the
 * browser. Best effort by construction: any failure here sends the page
 * without the tags, never fails or delays it beyond the session lookup's
 * own short timeout. */

import type { Handle } from '@sveltejs/kit';
import { renderSeoHead, SERVER_RENDERED, seoFor, type SeoSession } from '$lib/seo';
import { seoSessions } from '$lib/server/sessions';

const PLACEHOLDER = '<!--seo-head-->';

function needsSessions(path: string): boolean {
	return path === '/live' || path === '/sessions' || path.startsWith('/sessions/');
}

export const handle: Handle = async ({ event, resolve }) => {
	const path = event.url.pathname;
	let head = '';
	try {
		const sessions: SeoSession[] = needsSessions(path) ? await seoSessions() : [];
		const seo = seoFor(path, sessions);
		head = renderSeoHead(seo, { withTitle: !SERVER_RENDERED.has(seo.path) });
	} catch {
		head = '';
	}
	return resolve(event, {
		transformPageChunk: ({ html }) => html.replace(PLACEHOLDER, head)
	});
};
