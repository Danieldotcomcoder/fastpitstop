/** Pure selection logic for `/live`'s auto-detect polling — kept separate
 * from the page component so it has a test with no DOM, no fetch mock, and
 * no fake timers.
 *
 * `live` is `f1_api.sessions`' measured answer to "is this session happening
 * right now", derived from whether its event log is still growing. It is NOT
 * `provenance`, which only says the store is the source serving it and stays
 * true forever once a session has been ingested — this function used to read
 * that, so every visitor to `/live` was shown the previous race, indefinitely,
 * as though it were running.
 *
 * Picking the first one deliberately doesn't handle "two sessions live at
 * once" specially, since a real weekend never runs two concurrent
 * live-recorded sessions. */
import type { components } from './api/client';

type SessionSummary = components['schemas']['SessionSummaryOut'];

export function pickLiveSession(sessions: SessionSummary[]): SessionSummary | null {
	return sessions.find((session) => session.live === true) ?? null;
}
