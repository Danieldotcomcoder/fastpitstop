/** The one shape both radio surfaces render: the timeline lane (fed from
 * `radio_clip` WebSocket envelopes) and the corpus search page (fed from
 * `GET /radio/search` / `GET /sessions/{key}/radio`). Defined once here so
 * `RadioClipCard.svelte` never needs to know which transport a clip came
 * from — the same "one canonical shape, two converters" split
 * `$lib/delay-sync/envelope.ts` uses for `Envelope` itself. */
import type { Envelope } from '$lib/delay-sync';

export interface RadioClipView {
	driver: string | null;
	capturedAt: string | null;
	audioUrl: string | null;
	hasTranscript: boolean;
	transcriptText: string | null;
	language: string | null;
	confidence: number | null;
	lapNumber: number | null;
	position: number | null;
	trackStatus: string | null;
}

export type ConfidenceTier = 'good' | 'thin' | 'unreliable';

/** Mirrors `f1_radio.transcription.DEFAULT_LOW_CONFIDENCE_THRESHOLD` (0.5)
 * for the "thin"/"unreliable" boundary, with a higher `good` bar (0.75) so
 * the badge actually spans three tiers rather than pinning every real
 * transcript to `thin`. `null` (no transcript yet at all) renders no badge —
 * "no signal" is a different claim from "low confidence", the same
 * distinction `Stat`'s own `null` handling already makes. */
export function confidenceTier(confidence: number | null): ConfidenceTier | null {
	if (confidence === null) return null;
	if (confidence >= 0.75) return 'good';
	if (confidence >= 0.5) return 'thin';
	return 'unreliable';
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function asBoolean(value: unknown): boolean {
	return value === true;
}

/** `f1_api.stream.replay.radio_clip_envelope`'s payload, parsed. Returns
 * `null` for anything not shaped like a radio clip envelope — a caller
 * filters by `envelope.kind === 'radio_clip'` first, this only guards
 * against a malformed payload within one. */
export function radioClipViewFromEnvelope(envelope: Envelope): RadioClipView | null {
	const payload = envelope.payload;
	const driver = asString(payload.driver);
	const capturedAt = asString(payload.captured_at);
	if (driver === null && capturedAt === null) return null;
	return {
		driver,
		capturedAt,
		audioUrl: asString(payload.audio_url),
		hasTranscript: asBoolean(payload.has_transcript),
		transcriptText: asString(payload.transcript_text),
		language: asString(payload.language),
		confidence: asNumber(payload.confidence),
		lapNumber: asNumber(payload.lap_number),
		position: asNumber(payload.position),
		trackStatus: asString(payload.track_status)
	};
}

/** Structurally matches `f1_api.schemas.RadioClipOut` (and therefore the
 * generated `components['schemas']['RadioClipOut']`) without importing the
 * API layer's generated types — the same "define the shape again, let
 * structural typing satisfy it" choice `$lib/delay-sync/envelope.ts` makes
 * for `Envelope` itself, so this module stays a standalone, transport- and
 * API-layer-agnostic core. */
export interface RadioClipOutLike {
	driver: string | null;
	captured_at: string | null;
	audio_url: string | null;
	has_transcript: boolean;
	transcript_text: string | null;
	language: string | null;
	confidence: number | null;
	lap_number: number | null;
	position: number | null;
	track_status: string | null;
}

export function radioClipViewFromApi(clip: RadioClipOutLike): RadioClipView {
	return {
		driver: clip.driver,
		capturedAt: clip.captured_at,
		audioUrl: clip.audio_url,
		hasTranscript: clip.has_transcript,
		transcriptText: clip.transcript_text,
		language: clip.language,
		confidence: clip.confidence,
		lapNumber: clip.lap_number,
		position: clip.position,
		trackStatus: clip.track_status
	};
}

const TRACK_STATUS_LABELS: Record<string, string> = {
	green: 'Green',
	yellow: 'Yellow',
	sc: 'Safety car',
	vsc: 'Virtual safety car',
	red: 'Red flag',
	chequered: 'Chequered'
};

/** A track-status code (`f1_kernel.status`'s own vocabulary, lowercased) ->
 * a human label — shared by the timeline lane and the search results list
 * so the two surfaces never describe the same code two different ways.
 * Falls back to the raw code (title-cased) for anything not in the table
 * rather than hiding it — an unrecognized status is still worth showing. */
export function trackStatusLabel(status: string | null): string | null {
	if (status === null) return null;
	const known = TRACK_STATUS_LABELS[status.toLowerCase()];
	if (known) return known;
	return status.length > 0 ? status[0]!.toUpperCase() + status.slice(1) : status;
}
