/** The release gate's browser half (`just verify-real`, docs/ROADMAP.md W0):
 * real replayed races, from the local backfill under `data/historical/`,
 * through a real `f1_api` and a real frontend in Chromium. Its specs live in
 * `e2e-real/` so the fixture-backed suites never pick them up, and they skip
 * themselves when the backfill is absent (CI has no F1 data). */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { REAL_API_PORT, REAL_WEB_PORT } from './e2e/ports';

const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, '..', '..');
const API_BASE_URL = `http://127.0.0.1:${REAL_API_PORT}`;
const WEB_BASE_URL = `http://127.0.0.1:${REAL_WEB_PORT}`;

export default defineConfig({
	testDir: './e2e-real',
	fullyParallel: false,
	workers: 1,
	reporter: 'list',
	timeout: 120_000,
	expect: { timeout: 15_000 },
	use: { baseURL: WEB_BASE_URL, trace: 'retain-on-failure' },
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: `uv run f1-api --port ${REAL_API_PORT}`,
			cwd: REPO_ROOT,
			url: `${API_BASE_URL}/health`,
			// The real backfill, no store, and the production narrator.
			env: { F1_API_HISTORICAL_ROOT: 'data/historical', F1_API_CORS_ORIGINS: '*' },
			reuseExistingServer: false,
			timeout: 120_000,
			stdout: 'pipe',
			stderr: 'pipe'
		},
		{
			// The PRODUCTION build, served the way a deploy serves it: a
			// gate that measures frame pacing and layout shift on the dev
			// server measures Svelte's development checks, not the app.
			command: 'pnpm exec vite build && node build/index.js',
			url: WEB_BASE_URL,
			env: {
				PUBLIC_F1_API_BASE_URL: API_BASE_URL,
				PORT: String(REAL_WEB_PORT),
				HOST: '127.0.0.1',
				ORIGIN: WEB_BASE_URL
			},
			reuseExistingServer: false,
			timeout: 300_000
		}
	]
});
