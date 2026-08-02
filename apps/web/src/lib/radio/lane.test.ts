import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { RadioLaneReducer } from './lane';

function radioEnvelope(driver: string, capturedAt: string, text: string): Envelope {
	return {
		event_time_utc: capturedAt,
		kind: 'radio_clip',
		payload: {
			driver,
			captured_at: capturedAt,
			has_transcript: true,
			transcript_text: text,
			confidence: 0.9
		}
	};
}

function otherEnvelope(): Envelope {
	return { event_time_utc: '2024-05-01T14:00:00Z', kind: 'lap_completed', payload: { driver: '1' } };
}

describe('RadioLaneReducer', () => {
	it('ignores non-radio_clip envelopes', () => {
		const reducer = new RadioLaneReducer();
		reducer.update([otherEnvelope()]);
		expect(reducer.clips).toEqual([]);
	});

	it('collects radio_clip envelopes in arrival (event-time) order', () => {
		const reducer = new RadioLaneReducer();
		reducer.update([
			radioEnvelope('44', '2024-05-01T14:05:00Z', 'box box'),
			radioEnvelope('1', '2024-05-01T14:08:00Z', 'gap update')
		]);
		expect(reducer.clips.map((c) => c.driver)).toEqual(['44', '1']);
	});

	it('only processes newly-appended events on a later update (cursor-based fold)', () => {
		const reducer = new RadioLaneReducer();
		const first = [radioEnvelope('44', '2024-05-01T14:05:00Z', 'box box')];
		reducer.update(first);
		reducer.update([...first, radioEnvelope('1', '2024-05-01T14:08:00Z', 'gap update')]);
		expect(reducer.clips.map((c) => c.driver)).toEqual(['44', '1']);
	});

	it('deduplicates by (driver, capturedAt)', () => {
		const reducer = new RadioLaneReducer();
		const clip = radioEnvelope('44', '2024-05-01T14:05:00Z', 'box box');
		reducer.update([clip, clip]);
		expect(reducer.clips).toHaveLength(1);
	});

	it('reset clears accumulated clips and the cursor', () => {
		const reducer = new RadioLaneReducer();
		reducer.update([radioEnvelope('44', '2024-05-01T14:05:00Z', 'box box')]);
		reducer.reset();
		expect(reducer.clips).toEqual([]);
		reducer.update([radioEnvelope('44', '2024-05-01T14:05:00Z', 'box box')]);
		expect(reducer.clips).toHaveLength(1);
	});
});
