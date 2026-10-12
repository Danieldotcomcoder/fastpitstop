/** The race control feed on every layout, phone included (`just verify-real`,
 * docs/ROADMAP.md W4). A real race is replayed to the flag at phone and
 * desktop widths, and the "Radio & race control" panel must be on the page,
 * inside the screen's width, listing race control's messages, with its
 * "Race control" filter showing only those.
 *
 * Skips itself when the backfill is absent (CI has no F1 data). */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const BACKFILL = path.join(REPO_ROOT, 'data', 'historical', 'manifest.json');

test.skip(!fs.existsSync(BACKFILL), 'no backfill under data/historical (CI has no F1 data)');

const LAYOUTS = [
	{ name: 'phone', width: 390, height: 844 },
	{ name: 'desktop', width: 1440, height: 900 }
];

for (const layout of LAYOUTS) {
	test(`the race control feed is on the ${layout.name} layout`, async ({ page }) => {
		test.setTimeout(180_000);
		await page.setViewportSize({ width: layout.width, height: layout.height });
		await page.goto('/sessions/2024/1?speed=0');
		await expect(page.locator('.lapcount .caps')).toHaveText('Finished', { timeout: 150_000 });

		const feed = page.getByTestId('desk-feed');
		await feed.scrollIntoViewIfNeeded();
		await expect(feed).toBeVisible();
		const box = await feed.boundingBox();
		expect(box).not.toBeNull();
		expect(box!.x).toBeGreaterThanOrEqual(0);
		expect(box!.x + box!.width).toBeLessThanOrEqual(layout.width);

		// The 2024 Bahrain GP: race control sent dozens of messages.
		await feed.getByRole('button', { name: 'Race control' }).click();
		const messages = feed.locator('.rc-msg');
		expect(await messages.count()).toBeGreaterThanOrEqual(6);
		await expect(messages.first()).toBeVisible();
		expect((await messages.first().innerText()).trim()).not.toBe('');
		await expect(feed.locator('.radio-item')).toHaveCount(0);
	});
}
