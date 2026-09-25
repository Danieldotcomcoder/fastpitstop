import { describe, expect, it } from 'vitest';
import { pickLiveSession } from './live-session-picker';
import type { components } from './api/client';

type SessionSummary = components['schemas']['SessionSummaryOut'];

function session(overrides: Partial<SessionSummary>): SessionSummary {
	return {
		key: '2024-1-race',
		event_name: 'Bahrain Grand Prix',
		session_key: { year: 2024, round: 1, session_type: 'race' },
		provenance: 'historical',
		has_radio: false,
		has_telemetry: false,
		live_recorded: false,
		live: false,
		...overrides
	};
}

describe('pickLiveSession', () => {
	it('returns null when every session is historical', () => {
		const sessions = [session({ key: 'a' }), session({ key: 'b' })];
		expect(pickLiveSession(sessions)).toBeNull();
	});

	it('returns the live session when one is in progress', () => {
		const live = session({ key: 'live-one', provenance: 'live', live: true });
		const sessions = [session({ key: 'a' }), live, session({ key: 'b' })];
		expect(pickLiveSession(sessions)).toBe(live);
	});

	it('returns null for an empty list', () => {
		expect(pickLiveSession([])).toBeNull();
	});

	// The regression. `provenance === 'live'` only means the store is the
	// source serving this session, and stays true forever once it has been
	// ingested. Reading it here showed the previous race as the running one
	// for the entire following week, and opened a live stream against it.
	it('does not treat a finished-but-stored session as live', () => {
		const finished = session({
			key: '2026-14-race',
			provenance: 'live',
			live_recorded: true,
			live: false
		});
		expect(pickLiveSession([finished])).toBeNull();
	});

	it('picks the running session over a stored finished one', () => {
		const finished = session({ key: '2026-14-race', provenance: 'live', live: false });
		const running = session({ key: '2026-15-race', provenance: 'live', live: true });
		expect(pickLiveSession([finished, running])).toBe(running);
	});
});
