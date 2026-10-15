/**
 * Grand Prix pages (docs/ROADMAP.md S3): the weekend's sessions, a
 * countdown, what a stop costs at the circuit and how the previous race
 * there was run, rendered on the server for people searching "<race>
 * start time".
 *
 * The API runs on `python/f1_api/tests/fixtures/e2e_schedule.json`: this
 * season's Singapore weekend in progress (qualifying in an hour) and the
 * 2025 Bahrain weekend, long over. The kernel fixture holds the 2024
 * Bahrain race, the "previous race" for the 2025 page.
 */
import { expect, test } from '@playwright/test';

const year = new Date().getUTCFullYear();

test('a weekend in progress: countdown, sessions and a calendar file', async ({
	page,
	request
}) => {
	const html = await (await request.get(`/races/${year}/singapore`)).text();
	expect(html).toMatch(/<h1[^>]*>Singapore Grand Prix<\/h1>/);
	expect(html).toMatch(new RegExp(`<title>${year} Singapore Grand Prix: Schedule, Start Times`));
	expect(html).toContain('"@type":"SportsEvent"');
	expect(html).toContain('"subEvent"');

	await page.goto(`/races/${year}/singapore`);
	await expect(page.getByText('Qualifying starts in')).toBeVisible();
	const sessions = page.getByRole('list', { name: 'Sessions this weekend' }).getByRole('listitem');
	await expect(sessions).toHaveCount(5);
	await expect(sessions.nth(3)).toHaveClass(/next/);
	await expect(page.getByRole('img', { name: /Marina Bay Street Circuit outline/ })).toBeVisible();
	await expect(page.getByText('measured over 18 stops', { exact: false })).toBeVisible();

	const calendar = page.getByRole('link', { name: 'Add to calendar' });
	const ics = await (await request.get((await calendar.getAttribute('href'))!)).text();
	expect(ics.match(/BEGIN:VEVENT/g)).toHaveLength(5);
	expect(ics).toContain('SUMMARY:Singapore Grand Prix: Race');
});

test('a Grand Prix page shares its own card: a 1200x630 PNG (S6)', async ({ request }) => {
	const html = await (await request.get(`/races/${year}/singapore`)).text();
	const card = /<meta property="og:image" content="https:\/\/fastpitstop\.online([^"]+)"/.exec(
		html
	)?.[1];
	expect(card).toBe(`/races/${year}/singapore/og.png`);
	const response = await request.get(card!);
	expect(response.headers()['content-type']).toBe('image/png');
	const png = await response.body();
	expect(png.subarray(1, 4).toString()).toBe('PNG');
	expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
	expect((await request.get(`/races/${year}/no-such-race/og.png`)).status()).toBe(404);
});

test('a weekend that is over shows how the last race there was run, no result', async ({
	page
}) => {
	await page.goto('/races/2025/bahrain');
	await expect(page.getByText('The weekend is over.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Add to calendar' })).toHaveCount(0);
	const plan = page.getByRole('region', { name: 'How 2024 was run' });
	await expect(plan).toContainText('Of the 8 finishers, 8 stopped once.');
	await expect(plan).toContainText('Soft → Hard');
	await expect(page.locator('main')).not.toContainText(/won|winner|victory|podium|P1\b/i);
});

test('the home page links its next race to the Grand Prix page', async ({ page }) => {
	await page.goto('/');
	await page
		.getByRole('region', { name: 'Singapore Grand Prix' })
		.getByRole('link', { name: 'Singapore Grand Prix' })
		.click();
	await expect(page).toHaveURL(new RegExp(`/races/${year}/singapore$`));
});

test('an unknown race is a 404, and the page fits a phone', async ({ page, request }) => {
	expect((await request.get(`/races/${year}/nowhere`)).status()).toBe(404);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(`/races/${year}/singapore`);
	await expect(page.getByRole('heading', { name: 'The weekend' })).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - window.innerWidth
	);
	expect(overflow).toBe(0);
});
