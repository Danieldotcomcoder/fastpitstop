/** Visual baselines (`just verify-visual`; accept a reviewed change with
 * `just visual-approve`). Every screen at phone, tablet and desktop width,
 * in the dark theme (the default) and the light one, full page.
 *
 * Deterministic by construction: the checked-in fixture data, a calendar of
 * fixed dates, the browser's clock frozen a week before that calendar's
 * Grand Prix and its time zone set to UTC, reduced motion. The race desk
 * replays a race, which needs a running clock, so it is captured at its
 * last lap with its live-moving parts masked instead (the sync strip's clocks,
 * the map's cars) and the moment toasts hidden, since they expire on a timer. */
import { expect, test, type Browser, type Page } from '@playwright/test';
import { VISUAL_CALLS_WEB_PORT } from '../e2e/ports';

const FROZEN_NOW = new Date('2099-09-28T12:00:00Z');
/** The fixture calendar's "current" season is this year's. */
const SEASON = new Date().getFullYear();

const WIDTHS = [390, 768, 1440] as const;
const THEMES = ['dark', 'light'] as const;

const SCREENS = [
	{ name: 'home', path: '/' },
	{ name: 'live', path: '/live' },
	{ name: 'sessions', path: '/sessions' },
	{ name: 'circuits', path: '/circuits' },
	{ name: 'circuit', path: '/circuits/marina-bay' },
	{ name: 'grand-prix', path: `/races/${SEASON}/singapore` },
	{ name: 'standings', path: '/standings/2024' },
	{ name: 'about', path: '/about' },
	{ name: 'design', path: '/design' }
] as const;

async function open(
	browser: Browser,
	width: number,
	theme: (typeof THEMES)[number],
	freeze: boolean
): Promise<Page> {
	const context = await browser.newContext({
		viewport: { width, height: 900 },
		timezoneId: 'UTC',
		locale: 'en-GB',
		reducedMotion: 'reduce',
		colorScheme: theme
	});
	await context.addInitScript((light) => {
		try {
			if (light) localStorage.setItem('theme', 'light');
			else localStorage.removeItem('theme');
		} catch {
			/* storage blocked: the default theme stands */
		}
	}, theme === 'light');
	const page = await context.newPage();
	if (freeze) await page.clock.setFixedTime(FROZEN_NOW);
	return page;
}

/** The three web fonts (`src/app.html`, Google Fonts with `display=swap`). */
const FONTS = ['500 20px "Bricolage Grotesque"', '400 14px "Geist"', '400 14px "Geist Mono"'];

async function settle(page: Page): Promise<void> {
	await page.waitForLoadState('networkidle');
	// `document.fonts.ready` resolves at once if the font stylesheet has not
	// declared its faces yet: the run's first screen was once taken in the
	// fallback font. Wait until each family has a face actually loaded.
	await page.waitForFunction(
		async (fonts) =>
			(await Promise.all(fonts.map((f) => document.fonts.load(f)))).every(
				(faces) => faces.length > 0
			),
		FONTS,
		{ timeout: 30_000 }
	);
	await page.evaluate(() => document.fonts.ready);
}

for (const screen of SCREENS) {
	for (const width of WIDTHS) {
		for (const theme of THEMES) {
			test(`${screen.name} at ${width} px, ${theme}`, async ({ browser }) => {
				const page = await open(browser, width, theme, true);
				await page.goto(screen.path);
				await settle(page);
				await expect(page).toHaveScreenshot(`${screen.name}-${width}-${theme}.png`, {
					fullPage: true
				});
				await page.context().close();
			});
		}
	}
}

/** Moment toasts expire on a timer, so whether one is up at the capture
 * varies: hidden rather than masked, so the image never depends on it. */
const NO_TOASTS = '.toaster { display: none !important; }';

/** The desk replayed to its last lap, with nothing more arriving. */
async function deskAtTheFlag(page: Page, url: string): Promise<void> {
	await page.goto(url);
	await page.addStyleTag({ content: NO_TOASTS });
	// The fixture race (30 laps) has no end-of-session status, so its
	// end is its last lap, with nothing more arriving.
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 120_000 });
	// The replay's own end, not a lull in it: with the calls pair building
	// alongside, a slow analysis once paused the stream for 2 s mid-race.
	await expect(page.locator('.site-foot')).toContainText('Stream ended', { timeout: 120_000 });
	await expect
		.poll(
			async () => {
				const before = await page.getByTestId('session-visible-events').textContent();
				await page.waitForTimeout(2_000);
				return before === (await page.getByTestId('session-visible-events').textContent());
			},
			{ timeout: 60_000 }
		)
		.toBe(true);
}

const DESK_MASK = (page: Page) => [page.locator('canvas'), page.locator('section.sync')];

for (const width of WIDTHS) {
	for (const theme of THEMES) {
		test(`race desk at the flag at ${width} px, ${theme}`, async ({ browser }) => {
			test.setTimeout(180_000);
			const page = await open(browser, width, theme, false);
			await deskAtTheFlag(page, '/sessions/2024/1?speed=0');
			await settle(page);
			await expect(page).toHaveScreenshot(`race-desk-${width}-${theme}.png`, {
				fullPage: true,
				mask: DESK_MASK(page)
			});
			await page.context().close();
		});
	}
}

/** ROADMAP W5's screens, against the pair serving both call kinds: the
 * desk's calls (the Strategy tab on a phone) and the ledger page. */
const CALLS = `http://127.0.0.1:${VISUAL_CALLS_WEB_PORT}`;

for (const width of WIDTHS) {
	for (const theme of THEMES) {
		test(`race desk with calls at ${width} px, ${theme}`, async ({ browser }) => {
			test.setTimeout(180_000);
			const page = await open(browser, width, theme, false);
			await deskAtTheFlag(page, `${CALLS}/sessions/2024/1?speed=0`);
			if (width < 900) await page.getByRole('button', { name: 'Strategy' }).click();
			await expect(page.getByTestId('calls-panel')).toBeVisible();
			await settle(page);
			await expect(page).toHaveScreenshot(`race-desk-calls-${width}-${theme}.png`, {
				fullPage: true,
				mask: DESK_MASK(page)
			});
			await page.context().close();
		});

		test(`calls ledger at ${width} px, ${theme}`, async ({ browser }) => {
			const page = await open(browser, width, theme, true);
			await page.goto(`${CALLS}/sessions/2024/1/calls?upto=16`);
			await expect(page.getByTestId('call-scorecard')).toBeVisible({ timeout: 60_000 });
			await settle(page);
			await expect(page).toHaveScreenshot(`calls-ledger-${width}-${theme}.png`, {
				fullPage: true
			});
			await page.context().close();
		});
	}
}
