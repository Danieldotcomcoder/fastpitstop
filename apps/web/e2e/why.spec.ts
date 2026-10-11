/** Phase 5.5's E2E suite (`just verify-phase5`): a real browser against a
 * real running `f1_api` + frontend pair pointed at the checked-in kernel
 * fixture, narrated by the production template narrator (see
 * `../playwright.config.ts`) — no live session, no database, no network, no
 * API key.
 *
 * Asserts, per CLAUDE.md's Phase 5 deliverable:
 *   (a) the race's moments get grounded explanations with their evidence;
 *   (b) explanations appear at the right synced moment;
 *   (c) no explanation leaks ahead of the clock;
 *   (d) a moment with no supported cause is never a card: no guess, and no
 *       "no clear cause" filler either (Story 2.0).
 */
import { expect, test } from '@playwright/test';
import {
	WHY_E2E_DELAY_SECONDS,
	WHY_FALLBACK_TEXT,
	WHY_FINISH_TEXT,
	WHY_SAFETY_CAR_TEXT,
	whyRaceUrl,
	whyStoryUrl
} from './why-fixtures';

test.describe('why-engine timeline lane', () => {
	test('explanations stay gated until the delay elapses, then show the race', async ({
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

		// (a) The fixture race's safety car and finish, in the exact words the
		// template narrator writes from their numbers.
		const grounded = page.locator('[data-testid="explanation-card"][data-grounded="true"]');
		await expect(grounded.filter({ hasText: WHY_SAFETY_CAR_TEXT })).toHaveCount(1);
		await expect(grounded.filter({ hasText: WHY_FINISH_TEXT })).toHaveCount(1);

		// Expanding cited evidence lazy-fetches full sources from
		// GET /anomalies/{id}/explanation — grounding as a UX feature.
		const finish = grounded.filter({ hasText: WHY_FINISH_TEXT });
		await finish.getByTestId('evidence-toggle').click();
		const evidence = finish.getByTestId('cited-evidence');
		await expect(evidence).toBeVisible({ timeout: 10_000 });
		await expect(evidence.locator('[data-evidence-kind]').first()).toBeVisible();

		// (d) Every card explains its moment; none is filler.
		await expect(
			page.locator('[data-testid="explanation-card"][data-grounded="false"]')
		).toHaveCount(0);
		await expect(page.getByText(WHY_FALLBACK_TEXT)).toHaveCount(0);
	});

	test('usable at a mobile (390px) viewport', async ({ page }) => {
		test.setTimeout(60_000);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(whyRaceUrl({ delay: WHY_E2E_DELAY_SECONDS }));
		await expect(page.getByTestId('explanation-card').first()).toBeVisible({ timeout: 30_000 });
	});
});

test.describe('race story view', () => {
	test('tells the race in order with expandable evidence', async ({ page }) => {
		test.setTimeout(60_000);
		await page.goto(whyStoryUrl());

		const story = page.getByTestId('race-story');
		await expect(story).toBeVisible({ timeout: 30_000 });
		const cards = story.getByTestId('explanation-card');
		await expect(cards.first()).toBeVisible();
		expect(await cards.count()).toBeGreaterThan(1);

		await expect(story.getByText(WHY_SAFETY_CAR_TEXT)).toBeVisible();
		await expect(story.getByText(WHY_FINISH_TEXT)).toBeVisible();
		await expect(story.getByText(WHY_FALLBACK_TEXT)).toHaveCount(0);

		const finish = story.locator('[data-testid="explanation-card"]', {
			hasText: WHY_FINISH_TEXT
		});
		await finish.getByTestId('evidence-toggle').click();
		await expect(finish.getByTestId('cited-evidence')).toBeVisible({ timeout: 10_000 });
	});
});
