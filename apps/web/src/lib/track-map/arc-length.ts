/** Arc-length parameterization of the circuit outline: turning an (x, y)
 * point into "how far around the lap is this", as a fraction [0, 1). This
 * is what sector-dominance painting (`dominance.ts`) is built on — it never
 * needs the feed's own sector boundaries (which are timing splits, not
 * spatial ones), only positions projected onto the SAME outline the map
 * already draws. */
import type { Point } from './types';

export interface ArcLengthTable {
	points: readonly Point[];
	/** Cumulative distance up to and including `points[i]`, `cumulative[0] === 0`. */
	cumulative: readonly number[];
	totalLength: number;
}

/** `noUncheckedIndexedAccess` makes every array index read `T | undefined`;
 * every call site here indexes within a loop-invariant-guaranteed range, so
 * this documents that guarantee once instead of asserting it at each site. */
function at<T>(array: readonly T[], index: number): T {
	const value = array[index];
	if (value === undefined) {
		throw new RangeError(`index ${index} out of bounds (length ${array.length})`);
	}
	return value;
}

export function buildArcLengthTable(points: readonly Point[]): ArcLengthTable {
	if (points.length < 2) {
		throw new Error('an arc-length table needs at least two outline points');
	}
	const cumulative = [0];
	for (let i = 1; i < points.length; i++) {
		const [x0, y0] = at(points, i - 1);
		const [x1, y1] = at(points, i);
		cumulative.push(at(cumulative, i - 1) + Math.hypot(x1 - x0, y1 - y0));
	}
	return { points, cumulative, totalLength: at(cumulative, cumulative.length - 1) };
}

/** Where on the lap (x, y) sits, as a fraction of total distance in [0, 1).
 * Approximated as the arc-length of the NEAREST outline vertex — cheap
 * (linear scan over ~200 points) and accurate enough at the outline's own
 * resampling resolution; it does not project onto the nearest segment. */
export function projectToArcLength(table: ArcLengthTable, x: number, y: number): number {
	if (table.totalLength <= 0) return 0;
	let bestIndex = 0;
	let bestDistSq = Infinity;
	for (let i = 0; i < table.points.length; i++) {
		const [px, py] = at(table.points, i);
		const distSq = (px - x) ** 2 + (py - y) ** 2;
		if (distSq < bestDistSq) {
			bestDistSq = distSq;
			bestIndex = i;
		}
	}
	return at(table.cumulative, bestIndex) / table.totalLength;
}

/** The inverse of `projectToArcLength`: the (x, y) point at a given lap
 * fraction, wrapping outside [0, 1). Used to paint dominance-colored arcs
 * back onto the outline. */
export function pointAtArcLength(table: ArcLengthTable, fraction: number): Point {
	const wrapped = ((fraction % 1) + 1) % 1;
	const target = wrapped * table.totalLength;
	let segment = 0;
	while (segment + 2 < table.cumulative.length && at(table.cumulative, segment + 1) < target) {
		segment++;
	}
	const segStart = at(table.cumulative, segment);
	const segEnd = at(table.cumulative, segment + 1);
	const segLength = segEnd - segStart;
	const t = segLength <= 0 ? 0 : (target - segStart) / segLength;
	const [x0, y0] = at(table.points, segment);
	const [x1, y1] = at(table.points, segment + 1);
	return [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t];
}
