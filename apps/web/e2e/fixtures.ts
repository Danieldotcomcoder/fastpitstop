/** Shared constants for the E2E suite — mirrors `python/f1_api/tests/
 * api_fixture.py` exactly (session keys, what each fixture race carries) so
 * this suite exercises the SAME checked-in synthetic historical fixture
 * every other Phase 2/3 test layer already uses, not a second invented one. */

/** Bahrain GP (round 1): the fixture race with a safety car AND telemetry
 * (the one fixture race `BrakingPointDrift` has anything to show for) —
 * the primary session the dashboard flow is exercised against. */
export const SC_SESSION_KEY = '2024-1-race';

/** Saudi Arabian GP (round 2): the fixture's green-flag race, no telemetry. */
export const GREEN_SESSION_KEY = '2024-2-race';

/** `speed=0` (as-fast-as-possible ingest) plus a short delay makes the
 * dashboard's release schedule deterministic and fast: `f1_api.stream.
 * replay.replay_envelopes` remaps every envelope's `event_time_utc` to the
 * wall-clock instant it is sent (see that module's docstring), and at
 * `speed=0` every envelope shares that SAME instant — so nothing releases
 * until `DELAY_SECONDS` of real time has passed, then everything releases
 * essentially at once. That gives the spoiler-safety assertion a sharp,
 * fast, non-flaky before/after boundary instead of depending on the
 * fixture race's own simulated duration. */
export const E2E_SPEED = 0;
export const E2E_DELAY_SECONDS = 10;

export function raceUrl(
	key: string,
	opts: { speed?: number; delay?: number } = {}
): string {
	const params = new URLSearchParams({
		key,
		speed: String(opts.speed ?? E2E_SPEED),
		delay: String(opts.delay ?? E2E_DELAY_SECONDS)
	});
	return `/race?${params.toString()}`;
}
