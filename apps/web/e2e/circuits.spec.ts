/**
 * Circuit pages (docs/ROADMAP.md S2): every circuit, and one circuit's
 * measured figures, rendered on the server so a search engine reads them.
 *
 * The API runs on the Phase 3 kernel fixture, which holds the 2024 Bahrain
 * race, so Bahrain's page links one replay; the figures themselves come
 * from the shipped circuit files.
 */
import { expect, test } from '@playwright/test';

test('the index links every circuit, drawn from its outline', async ({ page }) => {
	await page.goto('/circuits');
	await expect(page.getByRole('heading', { level: 1 })).toHaveText('Every circuit, measured');
	const bahrain = page.getByRole('link', { name: /Bahrain International Circuit/ });
	await expect(bahrain.getByRole('img', { name: /outline/ })).toBeVisible();
	await bahrain.click();
	await expect(page).toHaveURL(/\/circuits\/bahrain$/);
});

test('a circuit page is rendered on the server, with its evidence', async ({ page, request }) => {
	const html = await (await request.get('/circuits/marina-bay')).text();
	expect(html).toMatch(/<h1[^>]*>Marina Bay Street Circuit<\/h1>/);
	expect(html).toMatch(/<title>Marina Bay Street Circuit: Pit Stop Time Loss \(30\.1 s\)/);
	expect(html).toContain('SportsActivityLocation');

	await page.goto('/circuits/marina-bay');
	const map = page.getByRole('img', { name: /Marina Bay Street Circuit, with its 19 corners/ });
	await expect(map).toBeVisible();
	await expect(map.locator('.corners text')).toHaveCount(19);
	await expect(map.locator('path.drs')).toHaveCount(4);
	await expect(page.getByText('measured over 18 stops')).toBeVisible();
	await expect(page.getByText(/highest of \d+ measured circuits/)).toBeVisible();
	await expect(page.getByText('9 reliable stints')).toBeVisible();
});

test('tyre wear from few stints is marked, never shown as certain', async ({ page }) => {
	// Albert Park's medium rests on four stints, its soft on none.
	await page.goto('/circuits/albert-park');
	await expect(page.getByText('4 reliable stints · thin')).toBeVisible();
	// Hungaroring's soft, on one stint.
	await page.goto('/circuits/hungaroring');
	await expect(page.getByText('1 reliable stint · too few to trust')).toBeVisible();
});

test('a race held here opens its replay, and names no result', async ({ page }) => {
	await page.goto('/circuits/bahrain');
	const races = page.getByRole('region', { name: 'Races here' });
	await expect(races).not.toContainText(/won|winner|victory|podium|P1\b/i);
	await races.getByRole('link', { name: /2024 Bahrain Grand Prix replay/ }).click();
	await expect(page).toHaveURL(/\/sessions\/2024\/1$/);
});

test('a circuit without a measurement says so', async ({ page }) => {
	await page.goto('/circuits/monaco');
	await expect(page.getByText(/Not measured here yet/)).toBeVisible();
	await expect(page.getByText(/highest|lowest/)).toHaveCount(0);
});

test('an unknown circuit is a 404', async ({ request }) => {
	expect((await request.get('/circuits/nowhere')).status()).toBe(404);
});

test('the page fits a phone', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/circuits/marina-bay');
	await expect(page.getByRole('heading', { name: 'Tyre wear here' })).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - window.innerWidth
	);
	expect(overflow).toBe(0);
});
