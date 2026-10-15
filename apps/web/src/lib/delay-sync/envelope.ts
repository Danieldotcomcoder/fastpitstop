/** The one shape Delay Sync operates on. Structurally identical to
 * `f1_api.schemas.StreamEnvelope` / `$lib/api/stream`'s `StreamEnvelope` —
 * defined again here (rather than imported) so this module stays a
 * standalone, framework- and API-layer-agnostic core: it never imports
 * anything from `$lib/api/*`, so it can be unit-tested with no SvelteKit
 * runtime and reused if the transport ever changes. Any object shaped like
 * `StreamEnvelope` satisfies this by TypeScript's structural typing with no
 * conversion needed. */
export interface Envelope {
	/** ISO 8601 UTC. Never stripped — this is the one field the whole
	 * spoiler-safety guarantee is gated on. */
	event_time_utc: string;
	/** A `DomainEvent.kind`, or the sentinel `"kernel_snapshot"`. */
	kind: string;
	payload: Record<string, unknown>;
}

/** `f1_api.stream.replay.KERNEL_SNAPSHOT_KIND` — kept in sync by hand, the
 * same discipline `$lib/api/stream`'s `StreamEnvelope` already follows,
 * since FastAPI's `openapi.json` carries no WebSocket contract to generate
 * this from. */
export const KERNEL_SNAPSHOT_KIND = 'kernel_snapshot';

/** `f1_api.stream.replay.kernel_snapshot_envelope`'s payload shape:
 * `{decision_lap, crossovers, rejoins}`, each crossover a
 * `CrossoverResultOut` and each rejoin a `RejoinProjectionOut`. Kept opaque
 * here (`unknown[]`) — Delay Sync's job stops at "don't release this before
 * its time," not at knowing what a crossover means. */
export interface KernelSnapshotPayload {
	decision_lap: number | null;
	crossovers: unknown[];
	rejoins?: unknown[];
}

export function isKernelSnapshot(envelope: Envelope): boolean {
	return envelope.kind === KERNEL_SNAPSHOT_KIND;
}

/** `f1_api.stream.replay.CAR_POSITIONS_KIND`: one instant's measured
 * position for every car, `{cars: {driver: [x, y]}}`. Kept in sync by hand
 * like `KERNEL_SNAPSHOT_KIND`. */
export const CAR_POSITIONS_KIND = 'car_positions';

export function isCarPositions(envelope: Envelope): boolean {
	return envelope.kind === CAR_POSITIONS_KIND;
}

/** Parses `event_time_utc` once, in one place, so every caller sorts and
 * compares the same way. Throws on an unparseable timestamp — a malformed
 * `event_time_utc` is a contract violation, not a value to guess about. */
export function eventTimeMs(envelope: Envelope): number {
	const ms = Date.parse(envelope.event_time_utc);
	if (Number.isNaN(ms)) {
		throw new Error(`Envelope has an unparseable event_time_utc: ${envelope.event_time_utc}`);
	}
	return ms;
}
