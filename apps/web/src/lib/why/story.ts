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
	/** The (driver, lap) whose completion made this moment knowable — the
	 * detector's `reveal_driver`/`reveal_lap`. A spoiler gate must wait for
	 * THIS lap, not `lapNumber`: a safety car is numbered with the lap it
	 * began on but describes everything up to its restart. `null` for an
	 * older server that does not send it. */
	revealDriver: string | null;
	revealLap: number | null;
}

/** Merge an anomaly list with any explanations already fetched (keyed by
 * anomaly id). Anomalies keep their API order (race order for the story
 * route); missing explanations stay `null` so the UI can show a
 * loading/pending state per row without inventing prose. */
export function buildStoryMoments(
	anomalies: readonly AnomalySummaryLike[],
	explanationsById: ReadonlyMap<string, ExplanationView>
): StoryMoment[] {
	return anomalies.map((anomaly) => {
		const revealDriver = anomaly.summary['reveal_driver'];
		const revealLap = anomaly.summary['reveal_lap'];
		return {
			anomalyId: anomaly.anomaly_id,
			kind: anomaly.kind,
			lapNumber: anomaly.lap_number,
			drivers: anomaly.drivers,
			anomalyTime: anomaly.anomaly_time,
			magnitude: anomaly.magnitude,
			salience: anomaly.salience,
			reasons: anomaly.reasons,
			explanation: explanationsById.get(anomaly.anomaly_id) ?? null,
			revealDriver: typeof revealDriver === 'string' ? revealDriver : null,
			revealLap: typeof revealLap === 'number' ? revealLap : null
		};
	});
}

/** Whether a story moment may show yet, given which laps have been
 * completed on the (already delay-gated) visible log. Waits for the
 * moment's own reveal lap when the server sent one; otherwise falls back to
 * any of its drivers completing `lapNumber`, and a moment with no lap at
 * all waits for the session to finish. */
export function storyMomentRevealed(
	moment: StoryMoment,
	isLapRevealed: (driver: string, lap: number) => boolean,
	sessionFinished: boolean
): boolean {
	if (moment.revealDriver !== null && moment.revealLap !== null) {
		return isLapRevealed(moment.revealDriver, moment.revealLap);
	}
	if (moment.lapNumber === null) return sessionFinished;
	const lap = moment.lapNumber;
	return moment.drivers.some((driver) => isLapRevealed(driver, lap));
}
