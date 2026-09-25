/** Phase 3.6's smoke + spoiler-safety E2E suite: a real browser against a
 * real running `f1_api` (see `../playwright.config.ts`'s `webServer`
 * entries) and the real `/race` dashboard, replaying the SAME checked-in
 * synthetic historical fixture `f1_kernel`/`f1_api`'s own test suites use.
 * No live session, no database, no `data/` needed.
 */
import { expect, test } from '@playwright/test';
import { E2E_DELAY_SECONDS, GREEN_SESSION_KEY, raceUrl, SC_SESSION_KEY } from './fixtures';

test.describe('race dashboard', () => {
	test('shows the picker and the unofficial disclaimer before any session is chosen', async ({
		page
	}) => {
		await page.goto('/race');
		await expect(page.getByRole('heading', { name: 'Race dashboard' })).toBeVisible();
		await expect(page.getByTestId('session-select')).toBeVisible();
		await expect(page.getByTestId('speed-select')).toBeVisible();
		await expect(
			page.getByText('is unofficial and is not associated', { exact: false })
		).toBeVisible();
		// Nothing race-specific renders until a session is chosen.
		await expect(page.getByTestId('tower-panel')).toHaveCount(0);
	});

	test('a deep-linked session drives the tower, map and undercut panels', async ({ page }) => {
		test.setTimeout(60_000);
		await page.goto(raceUrl(SC_SESSION_KEY));

		// The picker reflects the URL-driven connection (Playwright drives this
		// via the URL rather than the async-populated <select>s themselves).
		await expect(page.getByTestId('session-select')).toHaveValue(SC_SESSION_KEY);
		await expect(page.getByTestId('speed-select')).toHaveValue('0');
		await expect(page.getByText(`${E2E_DELAY_SECONDS}s delay`)).toBeVisible();

		// The map panel mounts and renders SOMETHING for every session. The
		// checked-in fixture race carries no `car_positions` table, so this
		// only asserts the panel's documented graceful-degradation state,
		// never car movement. See track-map-animation.spec.ts for an
		// animation proof against the synthetic preview `/track-map` ships.
		await expect(page.getByTestId('map-panel')).toBeVisible();
		await expect(page.getByTestId('map-panel')).toContainText('Track map');

		// Past the configured delay, the first batch of released envelopes
		// should populate the timing tower with real driver rows.
		const towerRows = page.getByTestId('tower-row');
		await expect(towerRows.first()).toBeVisible({ timeout: 20_000 });
		expect(await towerRows.count()).toBeGreaterThan(1);

		// This fixture race (round 1: safety car, several green-flag stops)
		// produces at least one undercut/overcut projection.
		await expect(page.getByTestId('undercut-panel')).toContainText(/undercut|overcut/i, {
			timeout: 20_000
		});
	});

	test("a session with no undercut battle yet shows the countdown panel's own waiting state", async ({
		page
	}) => {
		// Not asserting content here (timing-dependent) — just that the panel
		// is present and never blank/crashed before a battle has been projected.
		await page.goto(raceUrl(GREEN_SESSION_KEY));
		await expect(page.getByTestId('undercut-panel')).toBeVisible();
	});

	test('the switchable analytics panel switches between gap stream, degradation and braking', async ({
		page
	}) => {
		await page.goto(raceUrl(SC_SESSION_KEY));
		const panel = page.getByTestId('analytics-panel');

		await expect(panel.getByRole('tab', { name: 'Gap stream' })).toHaveAttribute(
			'aria-selected',
			'true'
		);
		await expect(panel.getByText('Gap to leader')).toBeVisible();

		await panel.getByRole('tab', { name: 'Degradation' }).click();
		await expect(panel.getByText('Tyre life, in lap time')).toBeVisible();
		await expect(panel.getByText('Gap to leader')).toHaveCount(0);

		await panel.getByRole('tab', { name: 'Braking' }).click();
		await expect(panel.getByText('The braking trace')).toBeVisible();
		await expect(panel.getByText('Tyre life, in lap time')).toHaveCount(0);
	});

	test('spoiler safety: the released event log is never ahead of the playback clock', async ({
		page
	}) => {
		test.setTimeout(70_000);
		await page.goto(raceUrl(SC_SESSION_KEY));

		async function sampleSyncState(): Promise<{
			lastEventMs: number;
			clockMs: number;
			visibleEvents: number;
		}> {
			const [lastEventText, clockText, visibleText] = await Promise.all([
				page.getByTestId('sync-last-event-ms').textContent(),
				page.getByTestId('sync-clock-ms').textContent(),
				page.getByTestId('sync-visible-events').textContent()
			]);
			return {
				lastEventMs: Number(lastEventText),
				clockMs: Number(clockText),
				visibleEvents: Number(visibleText)
			};
		}

		// The core guarantee (`TimelineBuffer.release`'s own contract, already
		// proven exhaustively by a 200-trial property test in vitest) exercised
		// here through the real running app: nothing released is ever ahead of
		// the playback clock it was released at, whenever sampled.
		function assertSpoilerSafe(sample: {
			lastEventMs: number;
			clockMs: number;
			visibleEvents: number;
		}): void {
			if (sample.visibleEvents > 0) {
				expect(sample.lastEventMs).toBeLessThanOrEqual(sample.clockMs);
			} else {
				expect(sample.lastEventMs).toBe(-1);
			}
		}

		// Sample well BEFORE the configured delay elapses: at speed=0 every
		// envelope shares one timestamp (see fixtures.ts), so nothing should
		// have released at all yet.
		await page.waitForTimeout(300);
		const early = await sampleSyncState();
		assertSpoilerSafe(early);
		expect(early.visibleEvents, 'nothing should release before the configured delay elapses').toBe(
			0
		);

		// Poll (never a fixed sleep) until the delay has clearly elapsed and
		// the whole batch has released, then re-check the same invariant.
		await expect
			.poll(async () => (await sampleSyncState()).visibleEvents, {
				timeout: 40_000,
				intervals: [500]
			})
			.toBeGreaterThan(0);
		const late = await sampleSyncState();
		assertSpoilerSafe(late);
		expect(late.visibleEvents).toBeGreaterThan(0);
	});
});
