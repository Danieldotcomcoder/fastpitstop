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
		...overrides
	};
}

describe('pickLiveSession', () => {
	it('returns null when every session is historical', () => {
		const sessions = [session({ key: 'a' }), session({ key: 'b' })];
		expect(pickLiveSession(sessions)).toBeNull();
	});

	it('returns the live session when one is in progress', () => {
		const live = session({ key: 'live-one', provenance: 'live' });
		const sessions = [session({ key: 'a' }), live, session({ key: 'b' })];
		expect(pickLiveSession(sessions)).toBe(live);
	});

	it('returns null for an empty list', () => {
		expect(pickLiveSession([])).toBeNull();
	});
});
