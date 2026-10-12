/** The release gate's browser half (`just verify-real`, docs/ROADMAP.md W0):
 * every session in REAL_SESSIONS replayed through the real stack, at phone
 * and desktop size, failing on what a viewer would notice:
 *
 * - any console error, page error, failed request or API error response;
 * - anything released before the configured delay (spoiler);
 * - the released log running ahead of the playback clock;
 * - layout shift (CLS) above MAX_CLS while data streams in, the timing
 *   tower's own row reordering aside;
 * - a story card that is filler ("no clear cause") instead of an
 *   explanation;
 * - and, on desktop at 1x replay, our main-thread work (script, layout,
 *   style) above MAX_MAIN_THREAD_SHARE of wall time, or any long task.
 *
 * Why main-thread work and not frame intervals: measured 2026-10-11, the
 * page's rAF intervals in headless Chromium miss a vsync one frame in
 * twenty even unthrottled, yet a trace shows the app's own work at about
 * 6% of the main thread and 555 of its 867 ms in Commit: headless
 * composites in software and re-copies the map canvas every frame, which a
 * phone's GPU-composited canvas does not. The gate measures what the app
 * controls.
 *
 * Skips itself when the backfill is absent. Car dots against the drawn
 * track are checked in Python (`deploy/check_outline_fit.py --gate`), which
 * measures every sample rather than a screenshot. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const BACKFILL = path.join(REPO_ROOT, 'data', 'historical', 'manifest.json');

/** One of each shape a race takes. Qualifying has its own spec
 * (`qualifying.spec.ts`): a different board, judged on its knockouts. */
export const REAL_SESSIONS = [
	{ year: 2024, round: 1, shape: 'measured car positions (Bahrain)' },
	{ year: 2024, round: 6, shape: 'safety car decides the race (Miami)' },
	{ year: 2024, round: 8, shape: 'street circuit, lap-1 red flag (Monaco)' },
	{ year: 2024, round: 21, shape: 'wet race with a red flag (São Paulo)' },
	{ year: 2025, round: 3, shape: 'positions in their own frame (Suzuka)' }
] as const;

const VIEWPORTS = [
	{ name: 'desktop', width: 1440, height: 900, mobile: false },
	{ name: 'phone', width: 390, height: 844, mobile: true }
] as const;

const DELAY_S = 6;
const MAX_CLS = 0.05;
const MAX_MAIN_THREAD_SHARE = 0.15;
const LONG_TASK_MS = 50;
const FALLBACK_TEXT = 'No clear cause found in the available evidence.';

test.skip(!fs.existsSync(BACKFILL), 'no backfill under data/historical (CI has no F1 data)');

/** Collects everything the gate fails on, from before the page loads. */
async function watch(page: Page): Promise<string[]> {
	const problems: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error') problems.push(`console: ${message.text()}`);
	});
	page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
	page.on('requestfailed', (request) => {
		const failure = request.failure()?.errorText ?? 'failed';
		// A navigation or teardown cancelling an in-flight request is not a fault.
		if (!failure.includes('ERR_ABORTED')) problems.push(`request: ${request.url()} ${failure}`);
	});
	page.on('response', (response) => {
		if (response.status() >= 400) problems.push(`HTTP ${response.status()}: ${response.url()}`);
	});
	// Layout shift, except rows moving inside the timing tower: reordering
	// when positions change is the tower's job (W2 animates it with
	// transforms, which do not shift layout). Everything else counts.
	await page.addInitScript(() => {
		const w = window as unknown as { __cls: number };
		w.__cls = 0;
		const inTower = (node: Node | null) => {
			const element = node instanceof Element ? node : (node?.parentElement ?? null);
			return element?.closest('[data-testid="timing-tower"]') !== null && element !== null;
		};
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries() as unknown as {
				value: number;
				hadRecentInput: boolean;
				sources?: { node: Node | null }[];
			}[]) {
				if (entry.hadRecentInput) continue;
				const sources = entry.sources ?? [];
				if (sources.length > 0 && sources.every((source) => inTower(source.node))) continue;
				w.__cls += entry.value;
			}
		}).observe({ type: 'layout-shift', buffered: true });
	});
	return problems;
}

async function visibleEvents(page: Page): Promise<number> {
	return Number(await page.getByTestId('session-visible-events').textContent());
}

for (const session of REAL_SESSIONS) {
	for (const viewport of VIEWPORTS) {
		test(`${session.year} R${session.round} ${viewport.name}: ${session.shape}`, async ({
			browser
		}) => {
			const context = await browser.newContext({
				viewport: { width: viewport.width, height: viewport.height },
				isMobile: viewport.mobile,
				hasTouch: viewport.mobile
			});
			const page = await context.newPage();
			const problems = await watch(page);
			// Taken BEFORE navigating: the app opens its stream before the
			// load event, so "since load" would undercount the delay.
			const requested = Date.now();
			await page.goto(`/sessions/${session.year}/${session.round}?speed=16&delay=${DELAY_S}`);

			// Spoiler: no race information before the delay has passed. The
			// pre-race grid and driver names may show at once (a replay sends
			// what came before lights out with past timestamps, exactly what a
			// viewer joining before the start sees); a completed lap or a story
			// card may not.
			await page.waitForTimeout(1_500);
			while (Date.now() - requested < (DELAY_S - 0.5) * 1_000) {
				expect(await page.getByTestId('session-lap').textContent(), 'a lap before the delay').toBe(
					'00'
				);
				expect(await page.getByTestId('explanation-card').count(), 'a card before the delay').toBe(
					0
				);
				await page.waitForTimeout(500);
			}

			// Then the race arrives, and the released log never runs ahead of
			// the playback clock (wall time minus the delay).
			await expect
				.poll(async () => page.getByTestId('session-lap').textContent(), { timeout: 60_000 })
				.not.toBe('00');
			for (let sample = 0; sample < 3; sample++) {
				await page.waitForTimeout(3_000);
				const last = await page.getByTestId('session-last-event').textContent();
				if (last) {
					const ahead = Date.parse(last) - (Date.now() - DELAY_S * 1_000);
					expect(ahead, 'released log ahead of the playback clock').toBeLessThanOrEqual(500);
				}
			}

			// Server and browser share this machine's clock, so any correction
			// the page applied must move it back (late), never ahead (early).
			const offset = Number(await page.getByTestId('session-clock-offset').textContent());
			expect(offset, `clock correction ${offset} ms`).toBeLessThanOrEqual(0);

			// The race is on screen, and every story card explains its moment.
			await expect(page.getByTestId('tower-row').first()).toBeVisible();
			expect(await page.getByTestId('tower-row').count()).toBeGreaterThanOrEqual(15);
			await expect(page.getByText(FALLBACK_TEXT)).toHaveCount(0);

			const cls = await page.evaluate(() => (window as unknown as { __cls: number }).__cls);
			expect(cls, `layout shift ${cls.toFixed(3)}`).toBeLessThanOrEqual(MAX_CLS);

			if (!viewport.mobile && session === REAL_SESSIONS[0]) {
				const { share, longTasks } = await mainThreadAtOneTimes(page, session);
				expect(share, `main-thread share ${(share * 100).toFixed(1)}%`).toBeLessThanOrEqual(
					MAX_MAIN_THREAD_SHARE
				);
				expect(longTasks, `long tasks: ${longTasks.join(', ')} ms`).toEqual([]);
			}

			expect(problems, problems.join('\n')).toEqual([]);
			await context.close();
		});
	}
}

/** Our main-thread work (script, layout, style recalculation) as a share
 * of five seconds of a 1x replay, and every task longer than LONG_TASK_MS. */
async function mainThreadAtOneTimes(
	page: Page,
	session: (typeof REAL_SESSIONS)[number]
): Promise<{ share: number; longTasks: number[] }> {
	const cdp = await page.context().newCDPSession(page);
	await page.goto(`/sessions/${session.year}/${session.round}?speed=1&delay=0`);
	await expect.poll(() => visibleEvents(page), { timeout: 60_000 }).toBeGreaterThan(0);
	await page.waitForTimeout(2_000);
	await cdp.send('Performance.enable');
	const metrics = async () =>
		Object.fromEntries(
			(await cdp.send('Performance.getMetrics')).metrics.map((m) => [m.name, m.value])
		) as Record<string, number>;
	await page.evaluate((threshold) => {
		const w = window as unknown as { __long: number[] };
		w.__long = [];
		new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) {
				if (entry.duration > threshold) w.__long.push(Math.round(entry.duration));
			}
		}).observe({ type: 'longtask' });
	}, LONG_TASK_MS);
	const before = await metrics();
	const started = Date.now();
	await page.waitForTimeout(5_000);
	const after = await metrics();
	const elapsed = (Date.now() - started) / 1_000;
	const work = (['ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration'] as const).reduce(
		(sum, key) => sum + ((after[key] ?? 0) - (before[key] ?? 0)),
		0
	);
	const longTasks = await page.evaluate(() => (window as unknown as { __long: number[] }).__long);
	return { share: work / elapsed, longTasks };
}
