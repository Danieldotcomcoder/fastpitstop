/** The session archive by weekend (ROADMAP W2): the season as a strip of
 * its circuits, then one card per round with every session it holds.
 * Fixture catalog: 2024 rounds 1 and 2 (races). The test calendar's 2024
 * season holds rounds 1 and 3 only: round 2 is left undated on purpose,
 * since `standings.spec.ts` needs the latest held race to have no date.
 * Round 3 is run but not held. */
import { expect, test } from '@playwright/test';

for (const width of [390, 1440]) {
	test(`the archive shows the season and its weekends, no result, at ${width} px`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/sessions');
		const strip = page.getByTestId('season-strip');
		await expect(strip.getByRole('link')).toHaveCount(2);
		await expect(page.getByRole('heading', { name: /2024 · 2 of 2 rounds run/i })).toBeVisible();
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
