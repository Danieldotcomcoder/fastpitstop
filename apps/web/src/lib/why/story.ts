/** Pure helpers for the race "story" view — salience-ranked narrative of
 * explained moments for one session. The story page itself fetches via
 * REST (`/sessions/{key}/anomalies` then `/anomalies/{id}/explanation`);
 * this module only owns the ranking/merge logic a component would
 * otherwise inline, so it stays unit-testable with no DOM. */
import type { ExplanationView } from './view';

export interface AnomalySummaryLike {
	anomaly_id: string;
	kind: string;
	lap_number: number | null;
	drivers: string[];
	anomaly_time: string;
	magnitude: number;
	salience: number;
	summary: Record<string, number | string | boolean | null>;
	reasons: string[];
}

export interface StoryMoment {
	anomalyId: string;
	kind: string;
	lapNumber: number | null;
	drivers: string[];
	anomalyTime: string;
	magnitude: number;
	salience: number;
	reasons: string[];
	explanation: ExplanationView | null;
}

/** Merge a salience-ranked anomaly list with any explanations already
 * fetched (keyed by anomaly id). Anomalies keep their API order
 * (most salient first); missing explanations stay `null` so the UI can
 * show a loading/pending state per row without inventing prose. */
export function buildStoryMoments(
	anomalies: readonly AnomalySummaryLike[],
	explanationsById: ReadonlyMap<string, ExplanationView>
): StoryMoment[] {
	return anomalies.map((anomaly) => ({
		anomalyId: anomaly.anomaly_id,
		kind: anomaly.kind,
		lapNumber: anomaly.lap_number,
		drivers: anomaly.drivers,
		anomalyTime: anomaly.anomaly_time,
		magnitude: anomaly.magnitude,
		salience: anomaly.salience,
		reasons: anomaly.reasons,
		explanation: explanationsById.get(anomaly.anomaly_id) ?? null
	}));
}
