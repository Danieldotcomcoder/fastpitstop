/** One call (ROADMAP W5) as the desk renders it: what was said about a
 * car the lap before it boxed, and how the race settled it. The one shape
 * a live `call` envelope and the REST ledger (`GET /sessions/{key}/calls`)
 * both become, so the scorecard, a call card and the ledger page read the
 * same thing. */
import type { components } from '$lib/api/schema';
import type { Envelope } from '$lib/delay-sync';

export type CallOut = components['schemas']['CallOut'];
export type CallKind = components['schemas']['CallKind'];
export type CallStatus = components['schemas']['CallStatus'];
export type CallConfidence = components['schemas']['Confidence'];

/** `f1_api.calls.CALL_KIND`. Kept in sync by hand, like the other stream
 * kinds: FastAPI's `openapi.json` carries no WebSocket contract. */
export const CALL_KIND = 'call';

const KINDS: readonly CallKind[] = ['undercut', 'rejoin'];
const STATUSES: readonly CallStatus[] = ['pending', 'right', 'wrong', 'void', 'no_call'];
const CONFIDENCES: readonly CallConfidence[] = ['high', 'medium', 'low'];

function str(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function int(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function num(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function bool(value: unknown): boolean | null {
	return typeof value === 'boolean' ? value : null;
}

/** A `call` envelope's payload, checked; null when it is not one. */
export function callFromEnvelope(envelope: Envelope): CallOut | null {
	if (envelope.kind !== CALL_KIND) return null;
	const p = envelope.payload;
	const callId = str(p.call_id);
	const driver = str(p.driver);
	const kind = str(p.kind) as CallKind | null;
	const status = str(p.status) as CallStatus | null;
	const confidence = str(p.confidence) as CallConfidence | null;
	const decisionLap = int(p.decision_lap);
	const inLap = int(p.in_lap);
	if (
		callId === null ||
		driver === null ||
		kind === null ||
		!KINDS.includes(kind) ||
		status === null ||
		!STATUSES.includes(status) ||
		confidence === null ||
		!CONFIDENCES.includes(confidence) ||
		decisionLap === null ||
		inLap === null
	) {
		return null;
	}
	return {
		call_id: callId,
		kind,
		driver,
		rival: str(p.rival),
		decision_lap: decisionLap,
		in_lap: inLap,
		confidence,
		status,
		detail: str(p.detail),
		window: str(p.window),
		crossover_lap: int(p.crossover_lap),
		projected_position: int(p.projected_position),
		projected_margin_s: num(p.projected_margin_s),
		rival_stop_lap: int(p.rival_stop_lap),
		driver_ahead_after: bool(p.driver_ahead_after),
		actual_position: int(p.actual_position),
		actual_car_ahead: str(p.actual_car_ahead)
	};
}

/** Whether a call counts on the scorecard: only right and wrong do. */
export function counts(call: CallOut): boolean {
	return call.status === 'right' || call.status === 'wrong';
}
