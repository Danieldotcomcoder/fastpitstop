/** The race desk per mockup A (ROADMAP W2): the battle card that leads it,
 * the race ribbon, the field line beside the map, at phone, tablet and
 * desktop widths. Same checked-in fixture race as the rest of
 * `verify-phase3` (2024 round 1, 30 laps, a safety car from lap 18). */
import { expect, test } from '@playwright/test';

test('the ribbon never runs ahead of the lap counter, and nothing shows before the delay', async ({
	page
}) => {
	test.setTimeout(120_000);
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(e.message));
	await page.setViewportSize({ width: 1440, height: 900 });
	// A delay long enough that a slow first load still lands inside it.
	await page.goto('/sessions/2024/1?speed=16&delay=20');
	const ribbon = page.getByTestId('race-ribbon');
	const reached = () => ribbon.locator('svg').getAttribute('data-reached').then(Number);
	const lap = async () => Number(await page.getByTestId('session-lap').textContent());

	// Held back by the delay: no lap, no line, no battle yet.
	await expect(ribbon).toBeVisible();
	expect(await reached()).toBe(0);
	await expect(ribbon.locator('polyline')).toHaveCount(0);
	await expect(page.getByTestId('battle-gap')).toHaveCount(0);

	// Then, as laps are released, the ribbon follows the lap counter and
	// never passes it.
	await expect.poll(reached, { timeout: 60_000 }).toBeGreaterThan(0);
	let last = 0;
	for (let sample = 0; sample < 10; sample++) {
		const [r, l] = [await reached(), await lap()];
		expect(r).toBeLessThanOrEqual(l);
		expect(r).toBeGreaterThanOrEqual(last);
		last = r;
		await page.waitForTimeout(1_000);
	}
	// A flat line has no height, so Playwright never calls it visible: count.
	expect(await ribbon.locator('polyline').count()).toBeGreaterThan(0);
	expect(errors).toEqual([]);
});

test('the battle leads the desk at the flag, with its trend, verdict and the field line', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/sessions/2024/1?speed=0&delay=0');
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 30_000 });
	const hero = page.getByTestId('battle-hero');
	await expect(hero).toContainText('The battle that matters');
	await expect(page.getByTestId('battle-gap')).toHaveText(/^\d+\.\d\d\s?s$/);
	await expect(hero.getByRole('img', { name: /^Gap from / })).toBeVisible();
	await expect(page.getByTestId('battle-verdict')).not.toBeEmpty();
	await expect(page.getByTestId('field-note')).toContainText(/leads/);
	await expect(page.getByTestId('model-record')).toContainText('85.3%');
	// The fixture race's safety car (laps 18 to 21) is shaded on the ribbon.
	await expect(page.getByTestId('race-ribbon').locator('rect.band')).toHaveCount(1);
	await expect(page.getByTestId('race-ribbon').locator('polyline')).toHaveCount(8);

	// Mockup A's three columns: the battle sits beside the tower, at the top.
	const heroBox = await hero.boundingBox();
	const towerBox = await page.getByTestId('tower-panel').boundingBox();
	expect(heroBox && towerBox && heroBox.x > towerBox.x + towerBox.width - 1).toBe(true);
	expect(heroBox && towerBox && Math.abs(heroBox.y - towerBox.y) < 2).toBe(true);
});

for (const width of [390, 768, 1024, 1440]) {
	test(`the desk fits at ${width} px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/sessions/2024/1?speed=0&delay=0');
		await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 30_000 });
		await expect
			.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
			.toBe(true);
		// Phones get the glance instead of the battle card; the ribbon sits
		// in the Strategy tab with the tower.
		if (width < 900) {
			await expect(page.getByTestId('battle-hero')).toHaveCount(0);
			await page.getByRole('button', { name: 'Strategy' }).click();
		}
		await expect(page.getByTestId('race-ribbon')).toBeVisible();
	});
}

test('Focus keeps the battle, next call, story, ribbon and top ten, and is remembered', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/sessions/2024/1?speed=0&delay=0');
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 30_000 });
	await expect(page.getByTestId('map-panel')).toBeVisible();

	await page
		.getByRole('group', { name: 'How much to show' })
		.getByRole('button', { name: 'Focus' })
		.click();
	await expect(page.getByTestId('map-panel')).toHaveCount(0);
	await expect(page.getByRole('tab', { name: 'Degradation' })).toHaveCount(0);
	for (const id of ['battle-hero', 'undercut-panel', 'desk-why', 'race-ribbon', 'tower-panel']) {
		await expect(page.getByTestId(id)).toBeVisible();
	}
	// The top ten only (the fixture race has eight cars, so all of them).
	await expect(page.getByTestId('tower-row').filter({ visible: true })).toHaveCount(8);

	await page.reload();
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 30_000 });
	await expect(page.getByTestId('map-panel')).toHaveCount(0);
	await page
		.getByRole('group', { name: 'How much to show' })
		.getByRole('button', { name: 'Full' })
		.click();
	await expect(page.getByTestId('map-panel')).toBeVisible();
});
