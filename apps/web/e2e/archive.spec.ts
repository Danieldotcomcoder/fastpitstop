/** The session archive by weekend (ROADMAP W2): the season as a strip of
 * its circuits, then one card per round with every session it holds.
 * Fixture catalog: 2024 rounds 1 and 2 (races); the test calendar's 2024
 * season adds round 3, run but not held. */
import { expect, test } from '@playwright/test';

for (const width of [390, 1440]) {
	test(`the archive shows the season and its weekends, no result, at ${width} px`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/sessions');
		const strip = page.getByTestId('season-strip');
		await expect(strip.getByRole('link')).toHaveCount(3);
		await expect(page.getByRole('heading', { name: /2024 · 3 of 3 rounds run/i })).toBeVisible();
		await expect(strip.getByRole('link', { name: /Round 1, Bahrain/ })).toHaveAttribute(
			'href',
			'/sessions/2024/1'
		);
		// Run, not held: its Grand Prix page, and marked as having no replay.
		const r3 = strip.getByRole('link', { name: /Round 3, Australian/ });
		await expect(r3).toHaveAttribute('href', '/races/2024/australian');
		await expect(r3).toContainText('no replay');

		const cards = page.getByTestId('weekend-card');
		await expect(cards).toHaveCount(2);
		await expect(cards.first()).toContainText('Round 2');
		await expect(cards.first().getByRole('link', { name: 'Race' })).toHaveAttribute(
			'href',
			'/sessions/2024/2'
		);
		await expect(page.locator('main')).not.toContainText(/winner|won |podium|P1\b/i);
		await expect
			.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
			.toBe(true);
	});
}
