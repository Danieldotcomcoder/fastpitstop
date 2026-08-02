/** Where a running `f1_api` instance lives (see docs/PHASE3.md — `just serve-api`).
 * `$env/dynamic/public` (not `static`) so `just web-dev` works with zero setup —
 * an unset var falls back to the default rather than failing the build. */
import { env } from '$env/dynamic/public';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';

export function apiBaseUrl(): string {
	const configured = env.PUBLIC_F1_API_BASE_URL;
	return (configured || DEFAULT_BASE_URL).replace(/\/+$/, '');
}

export function wsBaseUrl(): string {
	return apiBaseUrl().replace(/^http/, 'ws');
}

export interface BuildInfo {
	gitSha: string;
	buildTime: string;
}

/** This web image's own build metadata — the Dockerfile sets `PUBLIC_GIT_SHA`/
 * `PUBLIC_BUILD_TIME` as real env vars (not a Vite build-time replacement),
 * same as `PUBLIC_F1_API_BASE_URL` above, so the `/status` page shows what's
 * actually running without needing a rebuild to change. Unset (any non-
 * container run) reads the same "dev"/"unknown" defaults `f1_api.config`
 * uses for its own `GIT_SHA`/`BUILD_TIME`. */
export function webBuildInfo(): BuildInfo {
	return {
		gitSha: env.PUBLIC_GIT_SHA || 'dev',
		buildTime: env.PUBLIC_BUILD_TIME || 'unknown'
	};
}
