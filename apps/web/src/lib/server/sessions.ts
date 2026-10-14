/** The session list, read by the web server itself (page titles, the
 * sitemap, the server-rendered landing and archive pages).
 *
 * Cached briefly and bounded by a short timeout: this runs before a page's
 * HTML is sent, and a slow or unreachable API must cost a page its rich
 * title, never its load. On failure the last good list (or an empty one)
 * is returned. Once there is a list, an expired one is still served at
 * once while a fresh one is fetched behind it: a page's HTML waited up to
 * 2 s on a busy API every time the copy expired (ROADMAP W7). */

import { env as privateEnv } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';
import type { components } from '$lib/api/schema';
import { type SeoSession, toSeoSession } from '$lib/seo';

export type SessionRow = components['schemas']['SessionSummaryOut'];

const TTL_MS = 20_000;
const TIMEOUT_MS = 1_500;

let cached: { at: number; rows: SessionRow[] } | null = null;
let inflight: Promise<SessionRow[]> | null = null;

/** Inside the deployment the web server reaches the API directly
 * (`F1_API_INTERNAL_URL`, e.g. `http://api:8000`) rather than out through
 * the public proxy and back. */
function apiBase(): string {
	const base =
		privateEnv.F1_API_INTERNAL_URL || publicEnv.PUBLIC_F1_API_BASE_URL || 'http://127.0.0.1:8000';
	return base.replace(/\/+$/, '');
}

export async function sessionRows(): Promise<SessionRow[]> {
	if (cached && Date.now() - cached.at < TTL_MS) return cached.rows;
	const refresh = (inflight ??= (async () => {
		try {
			const response = await fetch(`${apiBase()}/sessions`, {
				signal: AbortSignal.timeout(TIMEOUT_MS)
			});
			if (!response.ok) throw new Error(`sessions: HTTP ${response.status}`);
			const rows = (await response.json()) as SessionRow[];
			cached = { at: Date.now(), rows };
			return rows;
		} catch {
			return cached?.rows ?? [];
		} finally {
			inflight = null;
		}
	})());
	return cached ? cached.rows : refresh;
}

export async function seoSessions(): Promise<SeoSession[]> {
	return (await sessionRows()).map(toSeoSession);
}
