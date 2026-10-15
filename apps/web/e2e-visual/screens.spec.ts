/** Visual baselines (`just verify-visual`; accept a reviewed change with
 * `just visual-approve`). Every screen at phone, tablet and desktop width,
 * in the dark theme (the default) and the light one, full page.
 *
 * Deterministic by construction: the checked-in fixture data, a calendar of
 * fixed dates, the browser's clock frozen a week before that calendar's
 * Grand Prix and its time zone set to UTC, reduced motion. The race desk
 * replays a race, which needs a running clock, so it is captured at its
 * last lap with its live-moving parts masked instead (the sync strip's clocks,
 * the map's cars, and the moment toasts, which expire on a timer). */
import { expect, test, type Browser, type Page } from '@playwright/test';

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

async function settle(page: Page): Promise<void> {
	await page.waitForLoadState('networkidle');
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

for (const width of WIDTHS) {
	for (const theme of THEMES) {
		test(`race desk at the flag at ${width} px, ${theme}`, async ({ browser }) => {
			test.setTimeout(180_000);
			const page = await open(browser, width, theme, false);
			await page.goto('/sessions/2024/1?speed=0');
			// The fixture race (30 laps) has no end-of-session status, so its
			// end is its last lap, with nothing more arriving.
			await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 120_000 });
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
			await settle(page);
			await expect(page).toHaveScreenshot(`race-desk-${width}-${theme}.png`, {
				fullPage: true,
				mask: [page.locator('canvas'), page.locator('section.sync'), page.locator('.toaster')]
			});
			await page.context().close();
		});
	}
}
