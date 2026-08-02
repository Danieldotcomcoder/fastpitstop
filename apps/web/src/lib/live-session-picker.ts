/** Pure selection logic for `/live`'s auto-detect polling — kept separate
 * from the page component so it has a test with no DOM, no fetch mock, and
 * no fake timers. `provenance === 'live'` is `f1_api.sessions`' own signal
 * that a key resolved to `StoreSource` (an in-progress session) rather than
 * historical backfill; picking the first one deliberately doesn't handle
 * "two sessions live at once" specially, since a real weekend never runs two
 * concurrent live-recorded sessions. */
import type { components } from './api/client';

type SessionSummary = components['schemas']['SessionSummaryOut'];

export function pickLiveSession(sessions: SessionSummary[]): SessionSummary | null {
	return sessions.find((session) => session.provenance === 'live') ?? null;
}
