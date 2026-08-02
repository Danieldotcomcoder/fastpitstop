/** Phase 5.5's E2E suite (`just verify-phase5`): a real browser against a
 * real running `f1_api` + frontend pair pointed at the checked-in kernel
 * fixture with `F1_API_WHY_FIXTURE` set (see `../playwright.config.ts`) —
 * no live session, no database, no network, no Anthropic key.
 *
 * Asserts, per CLAUDE.md's Phase 5 deliverable:
 *   (a) a known anomaly gets a grounded, cited explanation;
 *   (b) explanations appear at the right synced moment;
 *   (c) no explanation leaks ahead of the clock;
 *   (d) an ungrounded/weak case shows the safe fallback.
 */
import { expect, test } from '@playwright/test';
import {
	WHY_E2E_DELAY_SECONDS,
	WHY_FALLBACK_TEXT,
	WHY_GROUNDED_TEXT,
	whyRaceUrl,
	whyStoryUrl
} from './why-fixtures';

test.describe('why-engine timeline lane', () => {
	test('explanations stay gated until the delay elapses, then show grounded + fallback', async ({
		page
	}) => {
		test.setTimeout(60_000);
		await page.goto(whyRaceUrl());

		// (c) Well before the configured delay, nothing has released — the
		// lane shows its waiting state, never a leaked explanation.
		await page.waitForTimeout(300);
		await expect(page.getByTestId('explanation-lane')).toHaveCount(0);
		await expect(page.getByText('Waiting for explained moments', { exact: false })).toBeVisible();

		// Past the delay, speed=0 remaps every envelope to the same
		// event_time_utc, so every precomputed explanation releases together
		// once the playback clock catches up — (b).
		const cards = page.getByTestId('explanation-card');
		await expect(cards.first()).toBeVisible({ timeout: 30_000 });
		expect(await cards.count()).toBeGreaterThan(1);

		// (a) At least one grounded, cited explanation from the fixture's
		// race-control-backed position_change anomalies.
		const grounded = page.locator('[data-testid="explanation-card"][data-grounded="true"]');
		await expect(grounded.first()).toBeVisible();
		await expect(grounded.first()).toContainText(WHY_GROUNDED_TEXT);
		await expect(grounded.first().getByTestId('evidence-toggle')).toBeVisible();

		// Expanding cited evidence lazy-fetches full sources from
		// GET /anomalies/{id}/explanation — grounding as a UX feature.
		await grounded.first().getByTestId('evidence-toggle').click();
		const evidence = grounded.first().getByTestId('cited-evidence');
		await expect(evidence).toBeVisible({ timeout: 10_000 });
		await expect(evidence.locator('[data-evidence-kind]').first()).toBeVisible();

		// (d) Weak / low-evidence anomalies keep the honest fallback.
		const fallback = page.locator('[data-testid="explanation-card"][data-grounded="false"]');
		await expect(fallback.first()).toBeVisible();
		await expect(fallback.first()).toContainText(WHY_FALLBACK_TEXT);
		await expect(fallback.first()).toContainText('No clear cause', { exact: false });
	});

	test('usable at a mobile (390px) viewport', async ({ page }) => {
		test.setTimeout(60_000);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(whyRaceUrl({ delay: WHY_E2E_DELAY_SECONDS }));
		await expect(page.getByTestId('explanation-card').first()).toBeVisible({ timeout: 30_000 });
	});
});

test.describe('race story view', () => {
	test('salience-ranked narrative shows grounded moments and expandable evidence', async ({
		page
	}) => {
		test.setTimeout(60_000);
		await page.goto(whyStoryUrl());

		const story = page.getByTestId('race-story');
		await expect(story).toBeVisible({ timeout: 30_000 });
		const cards = story.getByTestId('explanation-card');
		await expect(cards.first()).toBeVisible();
		expect(await cards.count()).toBeGreaterThan(1);

		await expect(page.getByText(WHY_GROUNDED_TEXT).first()).toBeVisible();
		await expect(page.getByText(WHY_FALLBACK_TEXT).first()).toBeVisible();

		const grounded = story.locator('[data-testid="explanation-card"][data-grounded="true"]');
		await grounded.first().getByTestId('evidence-toggle').click();
		await expect(grounded.first().getByTestId('cited-evidence')).toBeVisible({ timeout: 10_000 });
	});
});
