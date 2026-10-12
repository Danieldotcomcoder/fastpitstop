/** The session archive (`just verify-real`, docs/ROADMAP.md W8): every card
 * carries its circuit's outline and no result, at phone and desktop width.
 * Rendered on the server from the real API over the local backfill.
 *
 * Skips itself when the backfill is absent (CI has no F1 data). */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const BACKFILL = path.join(REPO_ROOT, 'data', 'historical', 'manifest.json');

test.skip(!fs.existsSync(BACKFILL), 'no backfill under data/historical (CI has no F1 data)');

for (const width of [390, 1440]) {
	test(`archive cards show the circuit and no result at ${width} px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/sessions');
		const cards = page.locator('.session-card');
		await expect(cards.first()).toBeVisible();
		const count = await cards.count();
		expect(count).toBeGreaterThanOrEqual(10);

		const outlines = cards.locator('svg.track-thumbnail path.line');
		await expect(outlines).toHaveCount(count);
		for (const d of await outlines.evaluateAll((paths) => paths.map((p) => p.getAttribute('d')))) {
			expect(d?.length ?? 0).toBeGreaterThan(20);
		}
		await expect(page.getByText(/winner/i)).toHaveCount(0);
		await expect(cards.filter({ hasText: /practice_|sprint_qualifying/ })).toHaveCount(0);

		// Inside the screen: no card wider than the viewport.
		const right = await cards.evaluateAll((els) =>
			Math.max(...els.map((el) => el.getBoundingClientRect().right))
		);
		expect(right).toBeLessThanOrEqual(width);

		if (process.env.ARCHIVE_SHOTS) {
			await page.screenshot({ path: path.join(process.env.ARCHIVE_SHOTS, `archive-${width}.png`) });
		}
	});
}
