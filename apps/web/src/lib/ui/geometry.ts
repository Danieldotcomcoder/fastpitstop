/** The pure geometry behind the v2 components (ROADMAP W2 D2): sparkline
 * points, a circuit outline as an SVG path, stint bars and gap bars. No DOM,
 * so every shape is unit-tested; the components only draw what this
 * returns. */
import { fitOutlineToCanvas, project } from '$lib/track-map/geometry';
import type { Point } from '$lib/track-map/types';

const round = (v: number) => Math.round(v * 10) / 10;

/** SVG `points` strings for a series, one per unbroken run: a `null` value
 * is a gap in the data, so the line breaks there rather than joining across
 * it. Larger values sit higher. `domain` pins the value range (shared
 * between sparklines that are compared side by side); by default it is the
 * series' own range. */
export function sparkSegments(
	values: readonly (number | null)[],
	width: number,
	height: number,
	pad = 2,
	domain?: readonly [number, number]
): string[] {
	const known = values.filter((v): v is number => v !== null && Number.isFinite(v));
	if (known.length === 0) return [];
	const lo = domain?.[0] ?? Math.min(...known);
	const hi = domain?.[1] ?? Math.max(...known);
	const span = hi - lo;
	const step = values.length > 1 ? (width - 2 * pad) / (values.length - 1) : 0;
	const y = (v: number) =>
		span > 0 ? pad + (1 - (v - lo) / span) * (height - 2 * pad) : height / 2;
	const segments: string[] = [];
	let run: string[] = [];
	values.forEach((v, i) => {
		if (v === null || !Number.isFinite(v)) {
			if (run.length) segments.push(run.join(' '));
			run = [];
			return;
		}
		const x = values.length > 1 ? pad + i * step : width / 2;
		run.push(`${round(x)},${round(y(v))}`);
	});
	if (run.length) segments.push(run.join(' '));
	return segments;
}

/** A circuit outline as a closed SVG path fitted into `width`x`height`.
 * Outline coordinates are y-up, as the cars report them; SVG is y-down, so
 * the shape is flipped (as the race map does), never mirrored. */
export function outlinePath(
	points: readonly Point[],
	width: number,
	height: number,
	padding = 8
): string {
	if (points.length < 2) return '';
	const flipped: Point[] = points.map(([x, y]) => [x, -y]);
	const view = fitOutlineToCanvas(flipped, width, height, padding);
	const parts = flipped.map((p, i) => {
		const [x, y] = project(view, p);
		return `${i === 0 ? 'M' : 'L'}${round(x)} ${round(y)}`;
	});
	return `${parts.join(' ')} Z`;
}

export interface Stint {
	compound: string | null;
	/** First lap of the stint (1-based). */
	startLap: number;
	/** Last lap of the stint, or `null` while it is still running. */
	endLap: number | null;
}

export interface StintSegment {
	compound: string | null;
	fromLap: number;
	toLap: number;
	/** Percentages of the bar's width. */
	left: number;
	width: number;
}

/** Stints as segments of a bar. The bar spans the race distance when it is
 * known, and the laps run so far when it isn't (a replayed race does not
 * announce its distance, and the finishing lap count would give away a
 * shortened race). Nothing is drawn past `currentLap`: a stint still
 * running ends at the lap the viewer has reached. */
export function stintSegments(
	stints: readonly Stint[],
	currentLap: number,
	totalLaps: number | null
): StintSegment[] {
	if (currentLap <= 0) return [];
	const scale = Math.max(totalLaps ?? currentLap, currentLap);
	const out: StintSegment[] = [];
	for (const stint of [...stints].sort((a, b) => a.startLap - b.startLap)) {
		const from = Math.max(1, stint.startLap);
		const to = Math.min(stint.endLap ?? currentLap, currentLap);
		if (to < from) continue;
		out.push({
			compound: stint.compound,
			fromLap: from,
			toLap: to,
			left: ((from - 1) / scale) * 100,
			width: ((to - from + 1) / scale) * 100
		});
	}
	return out;
}

/** How full a gap bar is: the gap as a share of `max`, clamped to [0, 1]. */
export function gapFraction(gap: number | null, max: number): number {
	if (gap === null || !Number.isFinite(gap) || !(max > 0)) return 0;
	return Math.min(1, Math.max(0, gap / max));
}
