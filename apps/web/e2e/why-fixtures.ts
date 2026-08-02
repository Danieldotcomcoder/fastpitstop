/** Constants for Phase 5.5's E2E suite (`why.spec.ts`) — exercises the
 * SAME checked-in kernel historical fixture Phase 3 uses
 * (`python/f1_kernel/tests/fixtures/historical`, session `2024-1-race`),
 * with `F1_API_WHY_FIXTURE` enabling `f1_api.why_fixture.CitingNarrationModel`
 * so grounded explanations exist without a live API key. */
import { SC_SESSION_KEY, E2E_SPEED } from './fixtures';

/** Stable prose from `f1_api.why_fixture.GROUNDED_FIXTURE_TEXT` — must stay
 * in sync with that Python constant. */
export const WHY_GROUNDED_TEXT =
	'Race control confirms an on-track incident at this moment.';

/** Canonical `f1_why.narration.FALLBACK_TEXT`. */
export const WHY_FALLBACK_TEXT = 'No clear cause found in the available evidence.';

/** Slightly shorter than Phase 3's 10s so this suite stays snappy while
 * still giving a clear before/after Delay Sync boundary at `speed=0`. */
export const WHY_E2E_DELAY_SECONDS = 8;

export const WHY_SESSION_KEY = SC_SESSION_KEY;

export function whyRaceUrl(opts: { speed?: number; delay?: number } = {}): string {
	const params = new URLSearchParams({
		key: WHY_SESSION_KEY,
		speed: String(opts.speed ?? E2E_SPEED),
		delay: String(opts.delay ?? WHY_E2E_DELAY_SECONDS)
	});
	return `/race?${params.toString()}`;
}

export function whyStoryUrl(key: string = WHY_SESSION_KEY): string {
	return `/story?key=${encodeURIComponent(key)}`;
}
