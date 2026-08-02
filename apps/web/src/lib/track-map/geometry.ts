/** Projecting the circuit's own normalized coordinate space onto a canvas.
 * Kept separate from the renderer so the scale/fit math is unit-testable
 * without a `CanvasRenderingContext2D`. */
import type { Point, TrackOutline } from './types';

export interface Viewport {
	scale: number;
	offsetX: number;
	offsetY: number;
}

/** Fit `points`' own bounding box into a `width`x`height` canvas, preserving
 * aspect ratio (the real shape is never stretched to fill a mismatched
 * canvas) and centering it with at least `padding` px of margin on the
 * tighter axis. */
export function fitOutlineToCanvas(
	points: readonly Point[],
	width: number,
	height: number,
	padding = 24
): Viewport {
	if (points.length === 0) {
		return { scale: 1, offsetX: width / 2, offsetY: height / 2 };
	}
	let minX = Infinity;
	let maxX = -Infinity;
	let minY = Infinity;
	let maxY = -Infinity;
	for (const [x, y] of points) {
		if (x < minX) minX = x;
		if (x > maxX) maxX = x;
		if (y < minY) minY = y;
		if (y > maxY) maxY = y;
	}
	const shapeWidth = Math.max(maxX - minX, 1e-9);
	const shapeHeight = Math.max(maxY - minY, 1e-9);
	const availableWidth = Math.max(width - padding * 2, 1);
	const availableHeight = Math.max(height - padding * 2, 1);
	const scale = Math.min(availableWidth / shapeWidth, availableHeight / shapeHeight);
	return {
		scale,
		offsetX: width / 2 - ((minX + maxX) / 2) * scale,
		offsetY: height / 2 - ((minY + maxY) / 2) * scale
	};
}

export function project(viewport: Viewport, point: Point): Point {
	return [point[0] * viewport.scale + viewport.offsetX, point[1] * viewport.scale + viewport.offsetY];
}

/** Loosely-typed shape of `GET /sessions/{key}/circuit`'s `outline` field —
 * the generated client's tuple typing for `points` isn't guaranteed to line
 * up with `Point` bit-for-bit across `openapi-typescript` versions, so
 * callers pass the raw response through here rather than trusting it blindly. */
export interface ApiTrackOutline {
	points: readonly (readonly number[])[];
	width_m: number;
	height_m: number;
	source_points: number;
}

/** Reshapes a REST `TrackOutlineOut` response into this module's own
 * `TrackOutline` — the one seam where the API layer's loose types meet the
 * pure core's, the same "convert, invent nothing" role `f1_api.convert`
 * plays on the backend. Validates each point into a real 2-tuple at runtime
 * rather than assuming the network response's shape. Shared by every view
 * that fetches a circuit outline (`/track-map`'s dev harness, the race
 * dashboard) so there is exactly one conversion, not one per caller. */
export function outlineFromApi(raw: ApiTrackOutline | null | undefined): TrackOutline | null {
	if (!raw) return null;
	const points: Point[] = [];
	for (const point of raw.points) {
		const x = point[0];
		const y = point[1];
		if (typeof x === 'number' && typeof y === 'number') points.push([x, y]);
	}
	return { points, width_m: raw.width_m, height_m: raw.height_m, source_points: raw.source_points };
}
