/** ROADMAP W5's screens: the rejoin strip, "Our calls today", the latest
 * calls, the phone's Strategy tab and the ledger page. Production shows
 * none of them until a call kind passes its held-out backtest, so the
 * screens are driven against `playwright.config.ts`'s calls pair
 * (`F1_API_CALL_KINDS=undercut,rejoin`) and the gate itself against the
 * default pair. Same checked-in fixture race as the rest of
 * `verify-phase3` (2024 round 1, 30 laps, a safety car from lap 18). */
import { expect, test, type Page } from '@playwright/test';
import { CALLS_WEB_PORT } from './ports';

const CALLS = `http://127.0.0.1:${CALLS_WEB_PORT}`;

async function atTheFlag(page: Page, base = ''): Promise<void> {
	await page.goto(`${base}/sessions/2024/1?speed=0&delay=0`);
	await expect(page.getByTestId('session-lap')).toHaveText('30', { timeout: 60_000 });
}

/** The lap each call card names ("L13" -> 13). */
async function cardLaps(page: Page): Promise<number[]> {
	const labels = await page.getByTestId('call-card').locator('.lap').allTextContents();
	return labels.map((label) => Number(label.replace(/^L/, '')));
}

test('without a cleared call kind, the desk and the ledger show no calls', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await atTheFlag(page);
	await expect(page.getByTestId('undercut-panel')).toBeVisible();
	await expect(page.getByTestId('calls-panel')).toHaveCount(0);
	await expect(page.getByTestId('rejoin-strip')).toHaveCount(0);

	await page.goto('/sessions/2024/1/calls?upto=16');
	await expect(page.getByTestId('ledger-not-cleared')).toBeVisible({ timeout: 30_000 });
	await expect(page.getByTestId('call-card')).toHaveCount(0);
});

test('at the flag the desk shows where the focused car comes out, the score and the latest calls', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await atTheFlag(page, CALLS);
	const board = page.getByTestId('undercut-panel');
	await expect(board.getByTestId('rejoin-strip')).toBeVisible();
	await expect(board.getByTestId('rejoin-strip')).toContainText(
		/^If .+ boxes at the end of this lap/
	);
	await expect(board.getByTestId('rejoin-strip')).toContainText(/P\d+, /);

	const panel = page.getByTestId('calls-panel');
	await expect(panel).toBeVisible();
	await expect(panel.getByTestId('call-scorecard')).toContainText(/\d+\s*of\s*\d+\s*right/);
	expect(await panel.getByTestId('call-card').count()).toBeGreaterThan(0);
	const link = panel.getByTestId('calls-ledger-link');
	await expect(link).toHaveAttribute('href', /\/sessions\/2024\/1\/calls\?upto=30$/);
});

test('no call reaches the desk ahead of the playback clock', async ({ page }) => {
	test.setTimeout(150_000);
	await page.setViewportSize({ width: 1440, height: 900 });
	// A delay long enough that a slow first load still lands inside it.
	await page.goto(`${CALLS}/sessions/2024/1?speed=16&delay=20`);
	await expect(page.getByTestId('undercut-panel')).toBeVisible();
	// Held back: the panel needs a released lap's snapshot to appear at all.
	await expect(page.getByTestId('calls-panel')).toHaveCount(0);

	await expect(page.getByTestId('call-card').first()).toBeVisible({ timeout: 120_000 });
	for (let sample = 0; sample < 8; sample++) {
		const lap = Number(await page.getByTestId('session-lap').textContent());
		// A call locks when its car boxes, at the end of the lap it names.
		for (const called of await cardLaps(page)) expect(called).toBeLessThanOrEqual(lap);
		await page.waitForTimeout(1_000);
	}
});

test('the ledger is held to the lap watched and never shows the race length', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto(`${CALLS}/sessions/2024/1/calls?upto=16`);
	await expect(page.getByTestId('ledger-heading')).toHaveText(
		'Our calls in the Bahrain Grand Prix, to lap 16',
		{ timeout: 30_000 }
	);
	await expect(page.getByTestId('call-scorecard')).toBeVisible();
	const counted = page.getByTestId('ledger-counted').getByTestId('call-card');
	const waiting = page.getByTestId('ledger-waiting').getByTestId('call-card');
	const notCounted = page.getByTestId('ledger-not_counted').getByTestId('call-card');
	await expect(counted).toHaveCount(3);
	await expect(waiting).toHaveCount(3);
	await expect(notCounted).toHaveCount(7);
	for (const lap of await cardLaps(page)) expect(lap).toBeLessThanOrEqual(16);
	const text = (await page.locator('main').textContent()) ?? '';
	expect(text).not.toMatch(/\b30 laps\b|\/\s*30\b|of 30\b/);

	await page.getByRole('button', { name: 'One lap on' }).click();
	await expect(page.getByTestId('ledger-heading')).toHaveText(/to lap 17$/);
	await expect(page).toHaveURL(/upto=17/);

	await page.getByRole('button', { name: 'The whole race' }).click();
	await expect(page.getByTestId('ledger-heading')).toHaveText(
		'Our calls in the Bahrain Grand Prix'
	);
	await expect(page.getByTestId('ledger-waiting').getByTestId('call-card')).toHaveCount(0);
});

test('on a phone the Strategy tab leads with the rejoin call and the score', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await atTheFlag(page, CALLS);
	await page.getByRole('button', { name: 'Strategy' }).click();
	const panel = page.getByTestId('calls-panel');
	await expect(panel).toBeVisible();
	await expect(panel.getByTestId('rejoin-strip')).toBeVisible();
	await expect(panel.getByTestId('call-scorecard')).toBeVisible();
	// The board's own copy of the strip stays on wider screens only.
	await expect(page.getByTestId('undercut-panel').getByTestId('rejoin-strip')).toHaveCount(0);
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});
