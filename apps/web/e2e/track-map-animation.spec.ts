/** Proves the track map genuinely animates — the one dashboard assertion
 * that cannot be exercised against a real replayed historical session in
 * this repo: `f1_backfill` exports no position table at all
 * (DATA_CONTRACTS.md), so NO historical session anywhere ever carries
 * `car_position` events, and no shipped circuit has run the outline build
 * step against real telemetry yet either (docs/PHASE3.md's "Track map"
 * section). `/track-map`'s own dev harness (Phase 3.4) already solves
 * exactly this gap with a clearly-labeled "synthetic preview" — three cars
 * looping a stand-in oval through the SAME `DelaySyncController` ->
 * `TrackMap` pipeline a real session would use — so this drives THAT,
 * rather than inventing a second synthetic-data path on the real dashboard
 * (which would risk showing fabricated movement against a session a user
 * believes is real). `race dashboard`'s own spec (`dashboard.spec.ts`) only
 * asserts the map panel mounts and degrades gracefully for a real session,
 * never that it animates — see the comment there.
 */
import { expect, test } from '@playwright/test';

test.describe('track map', () => {
	test('the synthetic preview genuinely animates cars on the canvas', async ({ page }) => {
		test.setTimeout(30_000);
		await page.goto('/track-map');
		await expect(page.getByRole('heading', { name: 'Track map' })).toBeVisible();
		await expect(page.getByText('Synthetic data:', { exact: false })).toBeVisible();

		const canvas = page.getByRole('img', { name: 'Live track map' }).locator('canvas');
		await expect(canvas).toBeVisible();

		// Let the harness's own 2s initial delay clear (see /track-map's
		// `connectPreview`) so cars are actually moving on screen, not just
		// filling the buffer, before sampling.
		await page.waitForTimeout(3000);
		const first = await canvas.screenshot();
		await page.waitForTimeout(1000);
		const second = await canvas.screenshot();

		expect(Buffer.compare(first, second)).not.toBe(0);
	});
});
