import { expect, test } from '@playwright/test';

test('opening a session starts a moving replay, never the final classification', async ({
	page
}) => {
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(e.message));
	const hiddenRequests: string[] = [];
	page.on('request', (r) => {
		if (['fetch', 'xhr'].includes(r.resourceType()) && /\/(anomalies|radio)(\?|$|\/)/.test(r.url()))
			hiddenRequests.push(r.url());
	});
	await page.goto('/sessions/2024/1?speed=20&delay=2');
	const lap = page.getByTestId('session-lap');
	await expect(lap).toHaveText('00');
	await expect(page.getByTestId('tower-row')).toHaveCount(0);
	await expect
		.poll(async () => Number(await lap.textContent()), { timeout: 20000 })
		.toBeGreaterThan(0);
	const initial = Number(await lap.textContent());
	expect(initial).toBeLessThan(30);
	// The desk's tower names drivers by code and surname, as the design does.
	await expect(page.getByTestId('timing-tower')).toContainText('Verstappen');
	await expect(page.getByTestId('timing-tower')).toContainText('Piastri');
	await expect
		.poll(async () => Number(await lap.textContent()), { timeout: 15000 })
		.toBeGreaterThan(initial);
	await expect(page.getByTestId('tower-row')).toHaveCount(8);
	await expect(page.getByRole('link', { name: /spoiler.safe replay/i })).toHaveCount(0);
	await expect(page.getByRole('heading', { name: 'Race story' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Restart replay' }).click();
	await expect(lap).toHaveText('00');
	await expect(page.getByTestId('tower-row')).toHaveCount(0);
	await page.setViewportSize({ width: 390, height: 844 });
	await expect
		.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
		.toBe(true);
	expect(errors).toEqual([]);
	expect(hiddenRequests).toEqual([]);
});

test('braking shows the measured drift per corner, and says so when a driver has none', async ({
	page
}) => {
	await page.goto('/sessions/2024/1?speed=0&delay=1');
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 20000 });
	await page.getByRole('tab', { name: 'Braking drift' }).click();

	// The desk follows the fight for the lead, so the tab opens on the car in
	// second — which in this fixture carries no brake telemetry. That state
	// must say so plainly rather than draw an empty chart.
	await expect(
		page.getByRole('heading', { name: 'No corner-level braking data yet.' })
	).toBeVisible({ timeout: 30000 });

	// The leader is the one driver with a trace: exactly one measured corner
	// (turn 8), with its drift per lap of tyre age.
	await page.getByRole('group', { name: 'Driver' }).getByRole('button', { name: 'VER' }).click();
	const chart = page.getByRole('img', { name: 'Braking point drift by corner' });
	await expect(chart).toBeVisible({ timeout: 30000 });
	await expect(chart).toContainText('T8');
	await expect(chart).toContainText('m/lap');
});

test('missing braking data has one clear availability message', async ({ page }) => {
	// Round 2 of the fixture ships no car_telemetry at all.
	await page.goto('/sessions/2024/2?speed=0&delay=0');
	await expect(page.getByTestId('tower-row').first()).toBeVisible({ timeout: 20000 });
	await page.getByRole('tab', { name: 'Braking drift' }).click();
	await expect(
		page.getByRole('heading', { name: 'No corner-level braking data yet.' })
	).toBeVisible({ timeout: 30000 });
	await expect(page.getByRole('img', { name: 'Braking point drift by corner' })).toHaveCount(0);
});
