/** Sync 2.0 (ROADMAP W6) in the browser: pause and resume with the TV,
 * re-sync as the leader crosses the line, and the controls on a phone.
 * Catch-up needs a running session and is proven by the VM rehearsal
 * (docs/ROADMAP.md W6); its arithmetic is unit-tested in
 * `src/lib/delay-sync/sync2.test.ts`. Same checked-in fixture race as the
 * rest of `verify-phase3` (2024 round 1, 30 laps). */
import { expect, test, type Page } from '@playwright/test';

const lapOf = async (page: Page) => Number(await page.getByTestId('session-lap').textContent());
const released = async (page: Page) =>
	Number(await page.getByTestId('session-visible-events').textContent());

test('pause freezes the page with the TV, and resume carries on from there', async ({ page }) => {
	test.setTimeout(120_000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/sessions/2024/1?speed=16&delay=0');
	await expect.poll(() => lapOf(page), { timeout: 60_000 }).toBeGreaterThanOrEqual(3);

	const pause = page.getByTestId('sync-pause');
	await pause.click();
	await expect(pause).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByTestId('sync-quality')).toHaveText('Paused');
	const lap = await lapOf(page);
	const events = await released(page);
	const clock = await page.getByTestId('sync-clock').textContent();
	// The replay keeps streaming; nothing more reaches the page.
	await page.waitForTimeout(5_000);
	expect(await lapOf(page)).toBe(lap);
	expect(await released(page)).toBe(events);
	await expect(page.getByTestId('sync-clock')).toHaveText(clock ?? '');

	await pause.click();
	await expect(pause).toHaveAttribute('aria-pressed', 'false');
	// The delay grew by the pause, at least five seconds.
	const delay = Number.parseFloat((await page.getByTestId('shield-delay').textContent()) ?? '0');
	expect(delay).toBeGreaterThanOrEqual(5);
	await expect.poll(() => lapOf(page), { timeout: 30_000 }).toBeGreaterThan(lap);
});

test('a tap as the leader crosses the line re-syncs to that crossing', async ({ page }) => {
	test.setTimeout(120_000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto('/sessions/2024/1?speed=1&delay=0');
	const leader = page.getByTestId('sync-leader');
	await expect(leader).toBeEnabled({ timeout: 60_000 });
	await leader.click();
	await expect(page.locator('.sync-toast')).toContainText(/Synced to the leader finishing lap \d+/);
	await expect(page.getByTestId('sync-quality')).toHaveText('In sync');
	// The reason is the shield's tooltip on a wide screen.
	await expect(page.locator('.shield')).toHaveAttribute(
		'title',
		/from the leader finishing lap \d+/
	);
});

test('on a phone, pause stays on the folded strip', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/sessions/2024/1?speed=16&delay=0');
	const pause = page.getByTestId('sync-pause');
	await expect(pause).toBeVisible({ timeout: 60_000 });
	// The rest of the controls fold behind Adjust.
	await expect(page.getByTestId('sync-leader')).toBeHidden();
	const box = await pause.boundingBox();
	expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
	await page.getByRole('button', { name: 'Adjust' }).click();
	await expect(page.getByTestId('sync-leader')).toBeVisible();
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
});

test('a replay starts at 1x, and a new speed carries on without restarting', async ({ page }) => {
	test.setTimeout(150_000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const sockets: string[] = [];
	page.on('websocket', (ws) => {
		if (ws.url().includes('/stream/')) sockets.push(ws.url());
	});
	await page.goto('/sessions/2024/1?delay=0');
	const speeds = page.getByRole('group', { name: 'Replay speed' });
	await expect(speeds.getByRole('button', { name: '1×' })).toHaveAttribute('aria-pressed', 'true');
	await expect.poll(() => lapOf(page), { timeout: 60_000 }).toBeGreaterThanOrEqual(1);
	const before = await lapOf(page);

	await speeds.getByRole('button', { name: '16×' }).click();
	await expect(speeds.getByRole('button', { name: '16×' })).toHaveAttribute('aria-pressed', 'true');
	// The race moves on from where it was: the lap and the released log only
	// ever grow, and the page keeps its one connection.
	let lap = before;
	let count = await released(page);
	for (let sample = 0; sample < 30; sample++) {
		await page.waitForTimeout(1_000);
		const [l, c] = [await lapOf(page), await released(page)];
		expect(l).toBeGreaterThanOrEqual(lap);
		expect(c).toBeGreaterThanOrEqual(count);
		[lap, count] = [l, c];
	}
	expect(lap).toBeGreaterThanOrEqual(before + 2);
	await speeds.getByRole('button', { name: '4×' }).click();
	await page.waitForTimeout(2_000);
	expect(await lapOf(page)).toBeGreaterThanOrEqual(lap);
	expect(sockets).toHaveLength(1);
	expect(sockets[0]).toContain('speed=1');
});
