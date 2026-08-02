/** The one shape both why surfaces render: the timeline lane (fed from
 * `explanation` WebSocket envelopes) and the race story page (fed from
 * `GET /anomalies/{id}/explanation`). Defined once here so
 * `ExplanationCard.svelte` never needs to know which transport an
 * explanation came from — the same "one canonical shape, two converters"
 * split `$lib/radio/view.ts` already uses for radio clips. */
import type { Envelope } from '$lib/delay-sync';

export type ExplanationConfidence = 'high' | 'medium' | 'low';

export interface CitedEvidenceView {
	evidenceId: string;
	kind: string;
	href: string | null;
	summary: string | null;
	eventTime: string | null;
	confidence: number | null;
	confidenceLabel: string | null;
	drivers: string[];
}

export interface ExplanationView {
	anomalyId: string;
	kind: string;
	lapNumber: number | null;
	drivers: string[];
	anomalyTime: string | null;
	text: string;
	confidence: ExplanationConfidence;
	grounded: boolean;
	fallbackReason: string | null;
	citedEvidence: CitedEvidenceView[];
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
function asStringArray(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	return value.filter((item): item is string => typeof item === 'string');
}

function asConfidence(value: unknown): ExplanationConfidence {
	if (value === 'high' || value === 'medium' || value === 'low') return value;
	return 'low';
}

function citedFromUnknown(raw: unknown): CitedEvidenceView | null {
	if (raw === null || typeof raw !== 'object') return null;
	const item = raw as Record<string, unknown>;
	const evidenceId = asString(item.evidence_id);
	const kind = asString(item.kind);
	if (evidenceId === null || kind === null) return null;
	return {
		evidenceId,
		kind,
		href: asString(item.href),
		summary: asString(item.summary),
		eventTime: asString(item.event_time),
		confidence: asNumber(item.confidence),
		confidenceLabel: asString(item.confidence_label),
		drivers: asStringArray(item.drivers)
	};
}

/** `f1_api.stream.replay.explanation_envelope`'s payload, parsed. Returns
 * `null` for anything not shaped like an explanation envelope — a caller
 * filters by `envelope.kind === 'explanation'` first, this only guards
 * against a malformed payload within one. The WS payload is deliberately
 * lightweight (ids + hrefs only for citations); full evidence detail is
 * filled in later via `GET /anomalies/{id}/explanation`. */
export function explanationViewFromEnvelope(envelope: Envelope): ExplanationView | null {
	const payload = envelope.payload;
	const anomalyId = asString(payload.anomaly_id);
	const text = asString(payload.text);
	const kind = asString(payload.kind);
	if (anomalyId === null || text === null || kind === null) return null;
	const rawCited = Array.isArray(payload.cited_evidence) ? payload.cited_evidence : [];
	return {
		anomalyId,
		kind,
		lapNumber: asNumber(payload.lap_number),
		drivers: asStringArray(payload.drivers),
		anomalyTime: asString(envelope.event_time_utc),
		text,
		confidence: asConfidence(payload.confidence),
		grounded: asBoolean(payload.grounded),
		fallbackReason: asString(payload.fallback_reason),
		citedEvidence: rawCited
			.map(citedFromUnknown)
			.filter((item): item is CitedEvidenceView => item !== null)
	};
}

/** Structurally matches `f1_api.schemas.ExplanationOut` without importing
 * the generated API types — same discipline `$lib/radio/view.ts` uses for
 * `RadioClipOutLike`. */
export interface ExplanationOutLike {
	anomaly_id: string;
	kind: string;
	lap_number: number | null;
	drivers: string[];
	anomaly_time: string;
	text: string;
	confidence: ExplanationConfidence;
	grounded: boolean;
	fallback_reason: string | null;
	cited_evidence: Array<{
		evidence_id: string;
		kind: string;
		href: string | null;
		summary: string;
		event_time: string;
		confidence: number;
		confidence_label: string;
		drivers: string[];
	}>;
}

export function explanationViewFromApi(explanation: ExplanationOutLike): ExplanationView {
	return {
		anomalyId: explanation.anomaly_id,
		kind: explanation.kind,
		lapNumber: explanation.lap_number,
		drivers: explanation.drivers,
		anomalyTime: explanation.anomaly_time,
		text: explanation.text,
		confidence: explanation.confidence,
		grounded: explanation.grounded,
		fallbackReason: explanation.fallback_reason,
		citedEvidence: explanation.cited_evidence.map((item) => ({
			evidenceId: item.evidence_id,
			kind: item.kind,
			href: item.href,
			summary: item.summary,
			eventTime: item.event_time,
			confidence: item.confidence,
			confidenceLabel: item.confidence_label,
			drivers: item.drivers
		}))
	};
}

const KIND_LABELS: Record<string, string> = {
	pace_loss: 'Pace loss',
	off_track: 'Off track',
	position_change: 'Position change',
	pit_anomaly: 'Pit anomaly',
	deg_cliff: 'Degradation cliff',
	undercut_resolved: 'Undercut resolved'
};

const EVIDENCE_KIND_LABELS: Record<string, string> = {
	anomaly_fact: 'Anomaly facts',
	lap_context: 'Lap context',
	degradation_fit: 'Degradation fit',
	race_control: 'Race control',
	radio_clip: 'Team radio'
};

/** AnomalyKind / EvidenceKind wire values → short human labels shared by
 * the timeline lane and the story page so the two surfaces never describe
 * the same kind two different ways. Falls back to a title-cased raw code
 * rather than hiding an unrecognized kind. */
export function anomalyKindLabel(kind: string): string {
	const known = KIND_LABELS[kind];
	if (known) return known;
	return kind.length > 0 ? kind[0]!.toUpperCase() + kind.slice(1).replaceAll('_', ' ') : kind;
}

export function evidenceKindLabel(kind: string): string {
	const known = EVIDENCE_KIND_LABELS[kind];
	if (known) return known;
	return kind.length > 0 ? kind[0]!.toUpperCase() + kind.slice(1).replaceAll('_', ' ') : kind;
}

/** Canonical fallback text from `f1_why.narration.FALLBACK_TEXT` — kept in
 * sync so the UI can key off exact text for the honest "no clear cause"
 * state without a network round-trip. */
export const FALLBACK_TEXT = 'No clear cause found in the available evidence.';
