import type { RequestHandler } from './$types';
import favicon from '$lib/server/favicon.ico?inline';

/** `/favicon.ico`, which Bing and many other clients ask for by name. Served
 * from here rather than static/ because adapter-node's file server knows no
 * type for .ico and sent it with an empty Content-Type. Built by
 * `scripts/build-icons.mjs` (16, 32 and 48 px). */
const BYTES = Buffer.from(favicon.slice(favicon.indexOf(',') + 1), 'base64');

export const GET: RequestHandler = () =>
	new Response(new Uint8Array(BYTES), {
		headers: {
			'content-type': 'image/x-icon',
			'cache-control': 'public, max-age=86400'
		}
	});
