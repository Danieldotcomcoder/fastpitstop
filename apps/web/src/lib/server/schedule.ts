/** The next round of the calendar and its circuit outline, read by the web
 * server for the server-rendered home page (ROADMAP W3).
 *
 * Same contract as `./sessions`: cached briefly, bounded by a short
 * timeout, and a slow or unreachable API costs the page its next-race block,
 * never its load. */

import { env as privateEnv } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';
import type { components } from '$lib/api/schema';

export type ScheduleEvent = components['schemas']['ScheduleEventOut'];
export type Point = [number, number];

export interface NextRace {
	event: ScheduleEvent;
	/** The circuit's real outline, when a shipped circuit file has one. */
	outline: Point[] | null;
}

const TTL_MS = 10 * 60_000;
const TIMEOUT_MS = 1_500;

let cached: { at: number; value: NextRace | null } | null = null;
let inflight: Promise<NextRace | null> | null = null;

function apiBase(): string {
	const base =
		privateEnv.F1_API_INTERNAL_URL || publicEnv.PUBLIC_F1_API_BASE_URL || 'http://127.0.0.1:8000';
	return base.replace(/\/+$/, '');
}

async function getJson<T>(path: string): Promise<T> {
	const response = await fetch(`${apiBase()}${path}`, { signal: AbortSignal.timeout(TIMEOUT_MS) });
	if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
	return (await response.json()) as T;
}

async function outlineFor(circuitId: string | null): Promise<Point[] | null> {
	if (!circuitId) return null;
	try {
		const circuit = await getJson<components['schemas']['CircuitOut']>(
			`/circuits/${encodeURIComponent(circuitId)}`
		);
		const points = circuit.outline?.points ?? [];
		const valid = points.filter(
			(p): p is Point => typeof p[0] === 'number' && typeof p[1] === 'number'
		);
		return valid.length > 1 ? valid : null;
	} catch {
		return null;
	}
}

export async function nextRace(): Promise<NextRace | null> {
	if (cached && Date.now() - cached.at < TTL_MS) return cached.value;
	inflight ??= (async () => {
		try {
			const event = await getJson<ScheduleEvent | null>('/schedule/next');
			const value = event ? { event, outline: await outlineFor(event.circuit_id) } : null;
			cached = { at: Date.now(), value };
			return value;
		} catch {
			return cached?.value ?? null;
		} finally {
			inflight = null;
		}
	})();
	return inflight;
}
