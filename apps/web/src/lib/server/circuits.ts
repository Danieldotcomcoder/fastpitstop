/** Circuits, read by the web server for the server-rendered circuit pages,
 * their search tags and the sitemap (ROADMAP S2).
 *
 * Same contract as `./sessions`: cached, bounded by a short timeout, and a
 * slow API costs a page its data, never its load. Circuit data changes only
 * when a calibration tool runs, so the cache can be long. */

import { env as privateEnv } from '$env/dynamic/private';
import { env as publicEnv } from '$env/dynamic/public';
import type { components } from '$lib/api/schema';
import { toSeoCircuit, type SeoCircuit } from '$lib/seo';

export type CircuitSummary = components['schemas']['CircuitSummaryOut'];
export type CircuitProfile = components['schemas']['CircuitProfileOut'];

const TTL_MS = 10 * 60_000;
const TIMEOUT_MS = 2_500;

function apiBase(): string {
	const base =
		privateEnv.F1_API_INTERNAL_URL || publicEnv.PUBLIC_F1_API_BASE_URL || 'http://127.0.0.1:8000';
	return base.replace(/\/+$/, '');
}

const cache = new Map<string, { at: number; value: unknown }>();

/** The body at `path`; `null` for a 404. Throws when the API can't answer
 * and nothing is cached, so a caller can tell "no such circuit" from "try
 * again". */
async function cached<T>(path: string): Promise<T | null> {
	const hit = cache.get(path);
	if (hit && Date.now() - hit.at < TTL_MS) return hit.value as T;
	try {
		const response = await fetch(`${apiBase()}${path}`, {
			signal: AbortSignal.timeout(TIMEOUT_MS)
		});
		if (response.status !== 404 && !response.ok) {
			throw new Error(`${path}: HTTP ${response.status}`);
		}
		// A 404 isn't kept: any path can be asked for, so caching them would
		// let a crawler grow this map without bound.
		if (response.status === 404) return null;
		const value = (await response.json()) as T;
		cache.set(path, { at: Date.now(), value });
		return value;
	} catch (error) {
		if (hit) return hit.value as T;
		throw error;
	}
}

/** Every circuit; empty when the API can't be reached. */
export async function circuitList(): Promise<CircuitSummary[]> {
	try {
		return (await cached<CircuitSummary[]>('/circuits')) ?? [];
	} catch {
		return [];
	}
}

/** One circuit's page data; `null` when there is no such circuit. Throws
 * when the API can't be reached. */
export function circuitProfile(id: string): Promise<CircuitProfile | null> {
	return cached<CircuitProfile>(`/circuits/${encodeURIComponent(id)}`);
}

export async function seoCircuits(): Promise<SeoCircuit[]> {
	return (await circuitList()).map(toSeoCircuit);
}
