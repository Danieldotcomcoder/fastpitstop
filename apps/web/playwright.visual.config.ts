/** Visual baselines (`just verify-visual`, docs/ROADMAP.md W0): every screen
 * at 390, 768 and 1440 px in both themes, compared pixel by pixel with the
 * screenshots committed under `e2e-visual/`. A difference fails the run and
 * is accepted only by `just visual-approve`, after someone has looked at it.
 *
 * The production build, served as a deploy serves it, over the SAME
 * checked-in fixture the other suites use (no `data/`, no network), with a
 * calendar of fixed dates (`visual_schedule.json`) rather than the e2e
 * suite's dates relative to now, so a screen looks the same every day. */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';
import { VISUAL_API_PORT, VISUAL_WEB_PORT } from './e2e/ports';

const CONFIG_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(CONFIG_DIR, '..', '..');
const API_BASE_URL = `http://127.0.0.1:${VISUAL_API_PORT}`;
const WEB_BASE_URL = `http://127.0.0.1:${VISUAL_WEB_PORT}`;

export default defineConfig({
	testDir: './e2e-visual',
	fullyParallel: false,
	workers: 1,
	reporter: 'list',
	timeout: 120_000,
	expect: {
		timeout: 15_000,
		// Measured: identical runs differ by up to 98 pixels (2026-10-12) and
		// once 384 (2026-10-13, the circuits page), all inside display-font
		// glyphs; 2 px more padding on the archive's cards moved about 5,500
		// on every screen. 500 sits well between. (A ratio allowance would
		// have let a whole card through on a long page.)
		toHaveScreenshot: { maxDiffPixels: 500, animations: 'disabled' }
	},
	snapshotPathTemplate: '{testDir}/baselines/{arg}{ext}',
	use: { baseURL: WEB_BASE_URL, trace: 'retain-on-failure' },
	projects: [
		{
			name: 'chromium',
			use: {
				...devices['Desktop Chrome'],
				// Text rasterised the same way every run: without these,
				// glyph anti-aliasing moved ~100 pixels between identical runs.
				launchOptions: {
					args: [
						'--font-render-hinting=none',
						'--disable-lcd-text',
						'--disable-font-subpixel-positioning',
						'--force-color-profile=srgb'
					]
				}
			}
		}
	],
	webServer: [
		{
			command: `uv run f1-api --port ${VISUAL_API_PORT}`,
			cwd: REPO_ROOT,
			url: `${API_BASE_URL}/health`,
			env: {
				F1_API_HISTORICAL_ROOT: 'python/f1_kernel/tests/fixtures/historical',
				F1_API_SCHEDULE_FIXTURE: 'python/f1_api/tests/fixtures/visual_schedule.json',
				F1_API_CORS_ORIGINS: '*'
			},
			reuseExistingServer: false,
			timeout: 120_000,
			stdout: 'pipe',
			stderr: 'pipe'
		},
		{
			command: 'pnpm exec vite build && node build/index.js',
			url: WEB_BASE_URL,
			env: {
				PUBLIC_F1_API_BASE_URL: API_BASE_URL,
				PORT: String(VISUAL_WEB_PORT),
				HOST: '127.0.0.1',
				ORIGIN: WEB_BASE_URL
			},
			reuseExistingServer: false,
			timeout: 300_000
		}
	]
});
