import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import {
	confidenceTier,
	radioClipViewFromApi,
	radioClipViewFromEnvelope,
	trackStatusLabel
} from './view';

function radioEnvelope(payload: Record<string, unknown>): Envelope {
	return { event_time_utc: '2024-05-01T14:05:00Z', kind: 'radio_clip', payload };
}

describe('radioClipViewFromEnvelope', () => {
	it('parses a fully-transcribed clip', () => {
		const view = radioClipViewFromEnvelope(
			radioEnvelope({
				session_key: { year: 2024, round: 1, session_type: 'race' },
				driver: '44',
				audio_url: 'https://example.invalid/44.mp3',
				captured_at: '2024-05-01T14:05:00+00:00',
				has_transcript: true,
				transcript_text: 'Box box, box this lap',
				language: 'en',
				confidence: 0.91,
				lap_number: 12,
				position: 4,
				track_status: 'green',
				track_status_flags: []
			})
		);
		expect(view).toEqual({
			driver: '44',
			capturedAt: '2024-05-01T14:05:00+00:00',
			audioUrl: 'https://example.invalid/44.mp3',
			hasTranscript: true,
			transcriptText: 'Box box, box this lap',
			language: 'en',
			confidence: 0.91,
			lapNumber: 12,
			position: 4,
			trackStatus: 'green'
		});
	});

	it('degrades a not-yet-transcribed clip to has_transcript=false with null transcript fields', () => {
		const view = radioClipViewFromEnvelope(
			radioEnvelope({
				driver: '1',
				audio_url: 'https://example.invalid/1.mp3',
				captured_at: '2024-05-01T14:08:00+00:00',
				has_transcript: false,
				transcript_text: null,
				language: null,
				confidence: null,
				lap_number: null,
				position: null,
				track_status: null,
				track_status_flags: null
			})
		);
		expect(view?.hasTranscript).toBe(false);
		expect(view?.transcriptText).toBeNull();
		expect(view?.confidence).toBeNull();
	});

	it('returns null for a payload with neither a driver nor a captured_at (not a real clip)', () => {
		expect(radioClipViewFromEnvelope(radioEnvelope({}))).toBeNull();
	});
});

describe('radioClipViewFromApi', () => {
	it('maps every RadioClipOut field one-to-one', () => {
		const view = radioClipViewFromApi({
			driver: '44',
			captured_at: '2024-05-01T14:05:00Z',
			audio_url: null,
			has_transcript: true,
			transcript_text: 'front tyres are gone',
			language: 'en',
			confidence: 0.6,
			lap_number: 20,
			position: 3,
			track_status: 'sc'
		});
		expect(view).toEqual({
			driver: '44',
			capturedAt: '2024-05-01T14:05:00Z',
			audioUrl: null,
			hasTranscript: true,
			transcriptText: 'front tyres are gone',
			language: 'en',
			confidence: 0.6,
			lapNumber: 20,
			position: 3,
			trackStatus: 'sc'
		});
	});
});

describe('confidenceTier', () => {
	it('is null for an unknown confidence (no transcript yet)', () => {
		expect(confidenceTier(null)).toBeNull();
	});
	it('is good at or above 0.75', () => {
		expect(confidenceTier(0.9)).toBe('good');
		expect(confidenceTier(0.75)).toBe('good');
	});
	it('is thin between 0.5 and 0.75', () => {
		expect(confidenceTier(0.6)).toBe('thin');
		expect(confidenceTier(0.5)).toBe('thin');
	});
	it('is unreliable below 0.5 (matches f1_radio DEFAULT_LOW_CONFIDENCE_THRESHOLD)', () => {
		expect(confidenceTier(0.35)).toBe('unreliable');
		expect(confidenceTier(0)).toBe('unreliable');
	});
});

describe('trackStatusLabel', () => {
	it('maps known codes to human labels', () => {
		expect(trackStatusLabel('sc')).toBe('Safety car');
		expect(trackStatusLabel('green')).toBe('Green');
		expect(trackStatusLabel('VSC')).toBe('Virtual safety car');
	});
	it('falls back to a title-cased raw code for an unrecognized status', () => {
		expect(trackStatusLabel('mystery')).toBe('Mystery');
	});
	it('passes null through', () => {
		expect(trackStatusLabel(null)).toBeNull();
	});
});
