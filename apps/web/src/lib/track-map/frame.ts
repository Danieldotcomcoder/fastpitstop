/** Turns the accumulated race state (`RaceStateReducer`) into the plain,
 * already-resolved shape the Canvas renderer draws — interpolation,
 * dominance-to-arc conversion and team-color lookup all happen HERE, not in
 * the renderer, so the renderer stays pure drawing code and this stays
 * unit-testable with no `CanvasRenderingContext2D` at all. */
import { type ArcLengthTable, pointAtArcLength } from './arc-length';
import type { DominanceTracker } from './dominance';
import { DEFAULT_STALE_AFTER_MS, interpolatePosition } from './interpolation';
import type { RaceState } from './car-state';
import { colorForTeam } from './team-colors';
import type { Point } from './types';

export interface DrawSegment {
	start: Point;
	end: Point;
	color: string;
}

export interface DrawCar {
	driver: string;
	code: string | null;
	color: string;
	x: number;
	y: number;
	stale: boolean;
}

export interface DrawFrame {
	outline: readonly Point[] | null;
	segments: DrawSegment[];
	cars: DrawCar[];
}

export interface BuildDrawFrameOptions {
	/** Skip the interpolation tween and snap straight to each car's latest
	 * known sample — `prefers-reduced-motion`'s effect on this view. The
	 * data shown is identical either way; only the between-samples motion
	 * changes. */
	reducedMotion?: boolean
}

export function buildDrawFrame(
	outline: readonly Point[] | null,
	arcTable: ArcLengthTable | null,
	dominance: DominanceTracker | null,
	raceState: RaceState,
	renderTsMs: number,
	options: BuildDrawFrameOptions = {}
): DrawFrame {
	const reducedMotion = options.reducedMotion ?? false;
	return {
		outline,
		segments: arcTable && dominance ? dominanceSegments(arcTable, dominance, raceState) : [],
		cars: carsAt(raceState, renderTsMs, reducedMotion)
	};
}

function dominanceSegments(
	arcTable: ArcLengthTable,
	dominance: DominanceTracker,
	raceState: RaceState
): DrawSegment[] {
	const segments = dominance.segments;
	const drawn: DrawSegment[] = [];
	for (let i = 0; i < segments.length; i++) {
		const segment = segments[i];
		if (segment === undefined || segment.driver === null) continue;
		const team = raceState.cars.get(segment.driver)?.team ?? null;
		drawn.push({
			start: pointAtArcLength(arcTable, i / segments.length),
			end: pointAtArcLength(arcTable, (i + 1) / segments.length),
			color: colorForTeam(team)
		});
	}
	return drawn;
}

function carsAt(raceState: RaceState, renderTsMs: number, reducedMotion: boolean): DrawCar[] {
	const cars: DrawCar[] = [];
	for (const marker of raceState.cars.values()) {
		const position = reducedMotion
			? snappedPosition(marker.current, renderTsMs)
			: interpolatePosition(marker.current, marker.previous, renderTsMs);
		if (position === null) continue;
		cars.push({
			driver: marker.driver,
			code: marker.code,
			color: colorForTeam(marker.team),
			x: position.x,
			y: position.y,
			stale: position.stale
		});
	}
	return cars;
}

function snappedPosition(
	current: { x: number; y: number; tsMs: number } | null,
	renderTsMs: number
): { x: number; y: number; stale: boolean } | null {
	if (current === null) return null;
	return { x: current.x, y: current.y, stale: renderTsMs - current.tsMs > DEFAULT_STALE_AFTER_MS };
}
