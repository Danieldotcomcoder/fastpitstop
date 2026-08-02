/** Phase 4.5's E2E suite (`just verify-phase4`): a real browser against a
 * real running `f1_api` + frontend pair, pointed at the dedicated radio
 * fixture (see `../playwright.config.ts`'s Phase 4.5 `webServer` entries
 * and `radio-fixtures.ts`) — no live session, no real database (the API
 * runs with `F1_API_RADIO_FIXTURE` set, `f1_api.radio_fixture`'s DB-free
 * path), no network.
 *
 * Three things this proves, per CLAUDE.md's Phase 4 deliverable:
 *   (a) search returns the right clip for a paraphrased query it doesn't
 *       lexically match;
 *   (b) replay radio clips appear at the correct timeline position; and
 *   (c) no clip leaks ahead of the clock.
 */
import { expect, test } from '@playwright/test';
import {
	RADIO_EXPECTED_DRIVER_ORDER,
	RADIO_LOW_CONFIDENCE_TRANSCRIPT,
	RADIO_PARAPHRASE_EXPECTED_DRIVER,
	RADIO_PARAPHRASE_EXPECTED_TRANSCRIPT,
	RADIO_PARAPHRASE_QUERY,
	radioRaceUrl,
	radioSearchPageUrl
} from './radio-fixtures';

test.describe('team radio corpus search', () => {
	test('an empty search shows the idle hint, not a stale result list', async ({ page }) => {
		await page.goto(radioSearchPageUrl());
		await expect(page.getByText('Try:')).toBeVisible();
		await expect(page.getByTestId('radio-clip-card')).toHaveCount(0);
	});

	test('a paraphrased query with no lexical overlap finds the right clip, enriched with context', async ({
		page
	}) => {
		await page.goto(radioSearchPageUrl());
		await page.getByTestId('radio-search-query').fill(RADIO_PARAPHRASE_QUERY);

		const results = page.getByTestId('radio-clip-card');
		await expect(results.first()).toBeVisible({ timeout: 10_000 });
		await expect(results.first()).toHaveAttribute('data-driver', RADIO_PARAPHRASE_EXPECTED_DRIVER);
		await expect(results.first()).toContainText(RADIO_PARAPHRASE_EXPECTED_TRANSCRIPT);
		// Context enrichment (lap/position/track status), not just a bare
		// transcript.
		await expect(results.first()).toContainText('Lap 12');
		await expect(results.first()).toContainText('P4');
	});

	test('a driver filter excludes the closest-embedding clip when it belongs to someone else', async ({
		page
	}) => {
		await page.goto(radioSearchPageUrl());
		await page.getByTestId('radio-search-query').fill(RADIO_PARAPHRASE_QUERY);
		await page.getByText('Filters (driver, session, date range, track status)').click();
		await page.getByTestId('radio-filter-driver').fill('1');

		// The clip whose embedding is closest to this query belongs to driver
		// 44 (see radio-fixtures.ts); filtering to driver 1 must exclude it —
		// ranking only ever happens WITHIN the filtered candidate set, never
		// falling back to the globally-closest match regardless of filter.
		const results = page.getByTestId('radio-clip-card');
		await expect(results.first()).toBeVisible({ timeout: 10_000 });
		await expect(results).toHaveCount(2); // driver 1's own two fixture clips
		for (const card of await results.all()) {
			await expect(card).toHaveAttribute('data-driver', '1');
		}
		await expect(page.getByText(RADIO_PARAPHRASE_EXPECTED_TRANSCRIPT)).toHaveCount(0);
	});

	test('usable at a mobile (390px) viewport', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(radioSearchPageUrl());
		await page.getByTestId('radio-search-query').fill(RADIO_PARAPHRASE_QUERY);
		await expect(page.getByTestId('radio-clip-card').first()).toBeVisible({ timeout: 10_000 });
	});
});

test.describe('live/replay radio lane', () => {
	test('radio clips release in chronological order, never ahead of the playback clock', async ({
		page
	}) => {
		test.setTimeout(60_000);
		await page.goto(radioRaceUrl());

		// Well before the configured delay elapses, nothing has released —
		// the lane shows its own waiting state, never a leaked clip.
		await page.waitForTimeout(300);
		await expect(page.getByTestId('radio-lane')).toHaveCount(0);
		await expect(page.getByText('Waiting for team radio', { exact: false })).toBeVisible();

		// Past the delay, every fixture clip has released. speed=0 remaps
		// every envelope in the stream to the SAME event_time_utc (the
		// connection instant plus the delay — see radio-fixtures.ts's own
		// reasoning), so they all release together; ties keep arrival
		// (= chronological captured_at) order, which is the "correct
		// timeline position" this asserts.
		const cards = page.getByTestId('radio-clip-card');
		await expect(cards).toHaveCount(RADIO_EXPECTED_DRIVER_ORDER.length, { timeout: 30_000 });
		const drivers = await cards.evaluateAll((elements) =>
			elements.map((el) => el.getAttribute('data-driver'))
		);
		expect(drivers).toEqual(RADIO_EXPECTED_DRIVER_ORDER);

		// The planted low-confidence (safety-car) clip stays visibly marked,
		// never presented as an ordinary-confidence transcript.
		await expect(page.getByText(RADIO_LOW_CONFIDENCE_TRANSCRIPT)).toBeVisible();
		await expect(page.getByText('Low-confidence transcript', { exact: false })).toBeVisible();
	});
});
