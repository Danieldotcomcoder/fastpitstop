/** Re-sync at any moment of a race (ROADMAP W6): "tap as the leader
 * crosses the line". The leader's crossing of lap N is the first
 * `lap_completed` for lap N, by the stream's own timestamps, so the tap's
 * wall time minus that timestamp is the viewer's delay, exactly as for the
 * lights out tap. A race has a crossing every lap or so, and the one the
 * viewer just saw is the one nearest their current clock: a delay that is
 * off by less than half a lap picks the right one. */
import { eventTimeMs, type Envelope } from './envelope';

export interface Crossing {
	lap: number;
	/** The leader's crossing, epoch ms on the stream's clock. */
	eventMs: number;
}

export interface PickedCrossing extends Crossing {
	/** The delay that lines the clock up with this crossing at the tap. */
	delaySeconds: number;
}

/** A completed lap's crossing, or null for anything else. */
export function crossingOf(envelope: Envelope): Crossing | null {
	if (envelope.kind !== 'lap_completed') return null;
	const lap = envelope.payload.lap_number;
	if (typeof lap !== 'number' || !Number.isInteger(lap) || lap < 1) return null;
	const eventMs = eventTimeMs(envelope);
	return Number.isFinite(eventMs) ? { lap, eventMs } : null;
}

/** The leader's crossing of every lap seen in `envelopes`, lap order. */
export function leaderCrossings(envelopes: Iterable<Envelope>): Crossing[] {
	const first = new Map<number, number>();
	for (const envelope of envelopes) {
		const crossing = crossingOf(envelope);
		if (crossing === null) continue;
		const known = first.get(crossing.lap);
		if (known === undefined || crossing.eventMs < known) first.set(crossing.lap, crossing.eventMs);
	}
	return [...first].map(([lap, eventMs]) => ({ lap, eventMs })).sort((a, b) => a.lap - b.lap);
}

/** The crossing the viewer most likely just saw: the one whose delay is
 * nearest the current one, among those that give a delay within `bounds`.
 * Ties go to the earlier lap. Null when none qualifies. */
export function nearestCrossing(
	crossings: readonly Crossing[],
	tapWallMs: number,
	currentDelaySeconds: number,
	bounds: { min: number; max: number }
): PickedCrossing | null {
	let best: PickedCrossing | null = null;
	for (const crossing of crossings) {
		const delaySeconds = (tapWallMs - crossing.eventMs) / 1000;
		if (delaySeconds < bounds.min || delaySeconds > bounds.max) continue;
		const distance = Math.abs(delaySeconds - currentDelaySeconds);
		const bestDistance =
			best === null ? Infinity : Math.abs(best.delaySeconds - currentDelaySeconds);
		if (
			distance < bestDistance ||
			(distance === bestDistance && best !== null && crossing.lap < best.lap)
		) {
			best = { ...crossing, delaySeconds };
		}
	}
	return best;
}
