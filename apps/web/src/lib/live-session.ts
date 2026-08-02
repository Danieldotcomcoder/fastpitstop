/** The registry behind the header's persistent Delay Sync badge. A page that
 * connects to a streaming session (live or replay) registers its own
 * `DelaySyncController` here; `AppHeader` renders the badge/calibrator
 * whenever one is registered and hides it otherwise, so the "trust contract"
 * (CLAUDE.md's Prompt 1 brief: "never more than one interaction away") holds
 * without every page having to know about the header, and without the header
 * having to know about streaming.
 *
 * Deliberately the same hand-rolled `Store` the rest of Delay Sync uses, not
 * a Svelte store import — keeps this importable with zero Svelte runtime,
 * same as everything under `delay-sync/`. */
import type { DelaySyncController } from './delay-sync';
import { Store } from './delay-sync/store';

export interface ActiveStreamSession {
	controller: DelaySyncController;
	/** Human label for the calibrator popover, e.g. "Bahrain GP — Race (live)". */
	label: string;
	/** `event_time_utc` of the earliest envelope seen so far, for tap-to-sync.
	 * `null` until the first envelope arrives. */
	anchorEventTimeUtc: string | null;
}

export const activeStreamSession = new Store<ActiveStreamSession | null>(null);

export function setActiveStreamSession(session: ActiveStreamSession): void {
	activeStreamSession.set(session);
}

export function updateActiveStreamAnchor(anchorEventTimeUtc: string): void {
	const current = activeStreamSession.get();
	if (current === null || current.anchorEventTimeUtc !== null) return;
	activeStreamSession.set({ ...current, anchorEventTimeUtc });
}

export function clearActiveStreamSession(): void {
	activeStreamSession.set(null);
}
