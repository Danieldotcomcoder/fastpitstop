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
	const shield = page.getByRole('status').filter({ hasText: 'As of' });
	await expect(shield).toContainText('As of Bahrain, round 1');
	await expect(shield).toContainText('The latest race stays hidden');
	const drivers = page.getByRole('region', { name: 'Drivers' }).getByRole('listitem');
	await expect(drivers.first()).toContainText('VER');
	expect(Math.max(...asked)).toBeLessThanOrEqual(1);

	await shield.getByLabel('Count races up to').selectOption({ label: 'Saudi Arabian, round 2' });
	await expect(shield).toContainText('As of the last race you watched: Saudi Arabian, round 2');
	await expect(shield).toContainText('Every race so far is counted.');

	// Remembered on this device.
	await page.reload();
	await expect(page.getByRole('status').filter({ hasText: 'As of' })).toContainText(
		'As of the last race you watched: Saudi Arabian, round 2'
	);
	expect(asked).not.toContain(Infinity);
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
