/**
 * Standings (docs/ROADMAP.md M2) behind their own spoiler shield: points
 * count only up to the last race the viewer says they've watched, and
 * until they say, the latest race stays out. The page never asks the API
 * for more than that.
 *
 * The API runs on the Phase 3 kernel fixture: the 2024 Bahrain and Saudi
 * Arabian races, with no calendar for 2024, so neither race has a date and
 * the latest is treated as possibly unwatched.
 */
import { expect, test, type Page } from '@playwright/test';

function cutoffsAsked(page: Page): number[] {
	const asked: number[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (!url.pathname.endsWith('/standings')) return;
		const after = url.searchParams.get('after_round');
		// Every request names its cutoff: none may ask for "everything".
		asked.push(after === null ? Infinity : Number(after));
	});
	return asked;
}

test('the latest race stays hidden until the viewer says they watched it', async ({ page }) => {
	const asked = cutoffsAsked(page);
	await page.goto('/standings/2024');
	// The heading says what is counted; a notice names what is held back.
	const cutoff = page.getByTestId('standings-cutoff');
	await expect(cutoff).toHaveText(/After Bahrain, round 1\s*· 1 of 2 races counted/);
	const notice = page.getByTestId('standings-notice');
	await expect(notice).toContainText('Saudi Arabian, round 2 is not counted');
	const drivers = page.getByRole('region', { name: 'Drivers' }).getByRole('listitem');
	await expect(drivers.first()).toContainText('VER');
	expect(Math.max(...asked)).toBeLessThanOrEqual(1);

	// One click counts it.
	await notice.getByRole('button', { name: 'Count Saudi Arabian' }).click();
	await expect(cutoff).toHaveText(/After Saudi Arabian, round 2\s*· 2 of 2 races counted/);
	await expect(notice).toHaveCount(0);

	// Remembered on this device.
	await page.reload();
	await expect(page.getByTestId('standings-cutoff')).toContainText('After Saudi Arabian, round 2');
	expect(asked).not.toContain(Infinity);
});

test('any race can be chosen as the last one watched', async ({ page }) => {
	await page.goto('/standings/2024');
	await page.getByLabel('Count races up to').selectOption({ label: 'none yet' });
	await expect(page.getByTestId('standings-cutoff')).toHaveText('No race counted yet');
	await expect(page.getByTestId('standings-notice')).toContainText(
		"Counting up to the last race you said you've watched."
	);
});

test('the standings fit a phone', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/standings/2024');
	await expect(page.getByRole('heading', { name: 'Constructors' })).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - window.innerWidth
	);
	expect(overflow).toBe(0);
});
