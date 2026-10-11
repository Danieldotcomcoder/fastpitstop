/**
 * The weekend hub (docs/ROADMAP.md W3: "never empty"): between sessions the
 * home page and /live show the next Grand Prix, its sessions and a countdown
 * instead of a bare "nothing here".
 *
 * The API runs on `python/f1_api/tests/fixtures/e2e_schedule.json`
 * (`F1_API_SCHEDULE_FIXTURE`, times relative to now): a Singapore weekend in
 * progress, practice done, qualifying in an hour, the race in a day. The
 * last race comes from the Phase 3 kernel fixture's session list.
 */
import { expect, test, type Page } from '@playwright/test';

async function expectWeekend(page: Page): Promise<void> {
	const block = page.getByRole('region', { name: 'Singapore Grand Prix' });
	await expect(block).toBeVisible();
	await expect(block.getByText('This weekend')).toBeVisible();
	await expect(block.getByText('Marina Bay Street Circuit')).toBeVisible();
	await expect(block.getByText('Qualifying starts in')).toBeVisible();
	// Under an hour away: the clock form, never a days count.
	await expect(block.getByRole('timer')).toHaveText(/^00:\d\d:\d\d$/);
	const sessions = block.getByRole('list', { name: 'Sessions this weekend' }).getByRole('listitem');
	await expect(sessions).toHaveCount(5);
	await expect(sessions.nth(0)).toHaveClass(/done/);
	await expect(sessions.nth(3)).toHaveClass(/next/);
	await expect(sessions.nth(3)).toContainText('your time');
	// The circuit drawn from its real outline.
	await expect(block.getByRole('img', { name: /Marina Bay Street Circuit outline/ })).toBeVisible();
}

async function expectSpoilerFreeLastRace(page: Page): Promise<void> {
	const card = page.getByRole('region', { name: /Grand Prix|Round/ }).filter({
		hasText: 'Last race'
	});
	await expect(card).toBeVisible();
	await expect(card.getByRole('link', { name: 'Watch from the start' })).toHaveAttribute(
		'href',
		/\/sessions\/\d{4}\/\d+$/
	);
	await expect(card).not.toContainText(/won|winner|victory|podium|chequered|P1\b/i);
}

test('the home page leads with the weekend in progress, rendered on the server', async ({
	page
}) => {
	const html = await (await page.request.get('/')).text();
	expect(html).toContain('Singapore Grand Prix');
	expect(html).toContain('This weekend');

	await page.goto('/');
	await expectWeekend(page);
	await expectSpoilerFreeLastRace(page);
});

test('/live between sessions shows the next race, never an empty page', async ({ page }) => {
	await page.goto('/live');
	await expectWeekend(page);
	await expect(page.getByText('No session is running right now')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Browse race replays' })).toBeVisible();
});

test('both fit a phone', async ({ browser }) => {
	const context = await browser.newContext({
		viewport: { width: 390, height: 844 },
		isMobile: true,
		hasTouch: true
	});
	const page = await context.newPage();
	for (const path of ['/', '/live']) {
		await page.goto(path);
		await expect(page.getByRole('region', { name: 'Singapore Grand Prix' })).toBeVisible();
		const overflow = await page.evaluate(
			() => document.documentElement.scrollWidth - window.innerWidth
		);
		expect(overflow, `${path} scrolls sideways`).toBeLessThanOrEqual(0);
	}
	await context.close();
});
