/** Constants for Phase 5.5's E2E suite (`why.spec.ts`) — exercises the
 * SAME checked-in kernel historical fixture Phase 3 uses
 * (`python/f1_kernel/tests/fixtures/historical`, session `2024-1-race`),
 * narrated by the production `f1_why.TemplateNarrationModel`, which needs
 * no API key and no network. */
import { SC_SESSION_KEY, E2E_SPEED } from './fixtures';

/** What the template narrator writes for the fixture race's safety car and
 * finish — exact, because it is built from the race's own numbers. The
 * fixture's driver list carries no full names, so drivers are their codes. */
export const WHY_SAFETY_CAR_TEXT = 'Safety car from lap 18 to lap 21.';
export const WHY_FINISH_TEXT = 'VER took the chequered flag, 17.3 s ahead of PER, with LEC third.';

/** Canonical `f1_why.narration.FALLBACK_TEXT`: never a card any more. A
 * moment with nothing to say about it is left out of the lane and the
 * story rather than shown as "no clear cause". */
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
