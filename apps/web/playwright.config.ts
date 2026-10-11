/** Phase 3.6's E2E suite (`just verify-phase3`, `pnpm test:e2e`) — a real
 * browser against a real running `f1_api` and a real running frontend, both
 * pointed at the SAME checked-in synthetic historical fixture `f1_kernel`'s
 * and `f1_api`'s own test suites use (`python/f1_kernel/tests/fixtures/
 * historical`) so this needs no `data/`, no live session and no database,
 * the same "no data, no network" discipline `just verify-phase0/1/2` follow.
 *
 * Both processes are declared as `webServer` entries — Playwright starts
 * them, polls each `url` until it answers, and tears both down after the
 * run (or reuses whatever is already listening outside CI, so `pnpm
 * --filter @pitwall/web exec playwright test` also works against a dev
 * server you already have open). The API's `cwd` is the repo root (two
 * levels up from this config) so `F1_API_HISTORICAL_ROOT` resolves exactly
 * the way it does for a developer running `just serve-api` normally, and so
 * `uv run` finds the workspace's `pyproject.toml`/`uv.lock`.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { API_PORT, RADIO_API_PORT, RADIO_WEB_PORT, WEB_PORT } from './e2e/ports';

// This config lives at apps/web/playwright.config.ts — two directory levels
// below the repo root (apps/web -> apps -> repo root), NOT one: a single
// `path.dirname` off the file's own directory lands on `apps/`, which has no
// `python/` tree of its own, silently breaking the relative
// F1_API_HISTORICAL_ROOT below (uv itself still finds the workspace fine by
// searching upward, which is what made this easy to miss — only the
// spawned app's OWN relative-path reads were affected).
const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, '..', '..');
const API_BASE_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_BASE_URL = `http://127.0.0.1:${WEB_PORT}`;
const RADIO_API_BASE_URL = `http://127.0.0.1:${RADIO_API_PORT}`;
const RADIO_WEB_BASE_URL = `http://127.0.0.1:${RADIO_WEB_PORT}`;
const REUSE_EXISTING = !process.env.CI;

export default defineConfig({
	testDir: './e2e',
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	workers: 1,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	timeout: 60_000,
	expect: { timeout: 10_000 },
	use: {
		baseURL: WEB_BASE_URL,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: `uv run f1-api --port ${API_PORT}`,
			cwd: REPO_ROOT,
			url: `${API_BASE_URL}/health`,
			env: {
				F1_API_HISTORICAL_ROOT: 'python/f1_kernel/tests/fixtures/historical',
				// A calendar with a race weekend always in progress (times
				// relative to now), so the home page and /live are tested
				// between sessions without ever reaching FastF1.
				F1_API_SCHEDULE_FIXTURE: 'python/f1_api/tests/fixtures/e2e_schedule.json',
				F1_API_CORS_ORIGINS: '*'
				// Phase 5.5 (`just verify-phase5`) reuses this same pair with
				// the PRODUCTION narrator: `f1_why.TemplateNarrationModel`
				// needs no key and no network, so the suite asserts the
				// exact sentences a viewer reads.
			},
			reuseExistingServer: REUSE_EXISTING,
			timeout: 60_000,
			stdout: 'pipe',
			stderr: 'pipe'
		},
		{
			// `--host 127.0.0.1` pins the bind address to exactly what `url`
			// below polls — Vite's default `host: 'localhost'` resolves via
			// Node's DNS lookup, which on some systems (notably Windows) binds
			// only the IPv6 loopback and leaves 127.0.0.1 unreachable, so the
			// readiness check below would time out despite the server being up.
			command: `pnpm exec vite dev --port ${WEB_PORT} --host 127.0.0.1 --strictPort`,
			url: WEB_BASE_URL,
			env: {
				PUBLIC_F1_API_BASE_URL: API_BASE_URL
			},
			reuseExistingServer: REUSE_EXISTING,
			timeout: 60_000,
			stdout: 'pipe',
			stderr: 'pipe'
		},
		// Phase 4.5's own pair (`just verify-phase4`): a SEPARATE f1_api +
		// frontend, pointed at the dedicated radio E2E fixture
		// (`python/f1_radio/tests/fixtures/e2e_historical` +
		// `e2e_radio_fixture.json`) rather than the Phase 3 fixture above,
		// which carries no `team_radio_clip` events at all. A second frontend
		// process (not just a second API) because `PUBLIC_F1_API_BASE_URL` is
		// read once at boot — see `e2e/ports.ts`'s own docstring.
		{
			command: `uv run f1-api --port ${RADIO_API_PORT}`,
			cwd: REPO_ROOT,
			url: `${RADIO_API_BASE_URL}/health`,
			env: {
				F1_API_HISTORICAL_ROOT: 'python/f1_radio/tests/fixtures/e2e_historical',
				F1_API_RADIO_FIXTURE: 'python/f1_radio/tests/fixtures/e2e_radio_fixture.json',
				F1_API_SCHEDULE_FIXTURE: 'python/f1_api/tests/fixtures/e2e_schedule.json',
				F1_API_CORS_ORIGINS: '*'
			},
			reuseExistingServer: REUSE_EXISTING,
			timeout: 60_000,
			stdout: 'pipe',
			stderr: 'pipe'
		},
		{
			command: `pnpm exec vite dev --port ${RADIO_WEB_PORT} --host 127.0.0.1 --strictPort`,
			url: RADIO_WEB_BASE_URL,
			env: {
				PUBLIC_F1_API_BASE_URL: RADIO_API_BASE_URL,
				// The radio surfaces are off by default (no deployment has a
				// transcribed corpus) — but THIS pair is pointed at a fixture
				// that does, which is the whole point of the suite. See
				// `src/lib/features.ts`.
				PUBLIC_SHOW_RADIO: '1'
			},
			reuseExistingServer: REUSE_EXISTING,
			timeout: 60_000,
			stdout: 'pipe',
			stderr: 'pipe'
		}
	]
});
