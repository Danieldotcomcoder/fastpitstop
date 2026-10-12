/** A circuit page's map (ROADMAP S2): the real outline fitted to a box, with
 * each corner's number and each DRS zone placed by its distance round the
 * lap. Pure, so the server renders it as plain SVG.
 *
 * The outline is a reference lap starting at the line, so distance d sits
 * d / lap length of the way round it. */
import { buildArcLengthTable, pointAtArcLength } from '$lib/track-map/arc-length';
import { fitOutlineToCanvas, project } from '$lib/track-map/geometry';
import type { Point } from '$lib/track-map/types';

export interface CornerMark {
	label: string;
	x: number;
	y: number;
}

export interface CircuitMap {
	path: string;
	corners: CornerMark[];
	drs: string[];
	/** The line, and the direction of travel there in degrees (SVG's
	 * clockwise-from-x), so the mark can be drawn across the track. */
	start: { x: number; y: number; angle: number };
}

const round = (v: number) => Math.round(v * 10) / 10;

function toPath(points: readonly Point[], closed: boolean): string {
	const parts = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${round(x)} ${round(y)}`);
	return parts.join(' ') + (closed ? ' Z' : '');
}

export function circuitMap(
	outline: readonly Point[],
	lapDistanceM: number,
	corners: readonly { number: number; letter?: string | null; distance_m: number }[],
	drsZones: readonly { start_m: number; end_m: number }[],
	width: number,
	height: number,
	padding = 18
): CircuitMap | null {
	if (outline.length < 2 || !(lapDistanceM > 0)) return null;
	// y-up as the cars report it; SVG is y-down, so flip rather than mirror.
	const flipped: Point[] = outline.map(([x, y]) => [x, -y]);
	const view = fitOutlineToCanvas(flipped, width, height, padding);
	const screen = flipped.map((p) => project(view, p));
	const table = buildArcLengthTable([...screen, screen[0]!]);
	const at = (distance: number): Point => {
		const fraction = (((distance % lapDistanceM) + lapDistanceM) % lapDistanceM) / lapDistanceM;
		return pointAtArcLength(table, fraction);
	};
	const segment = (from: number, to: number): string => {
		const steps = Math.max(2, Math.ceil(((to - from) / lapDistanceM) * 120));
		const points = Array.from({ length: steps + 1 }, (_, i) =>
			at(from + ((to - from) * i) / steps)
		);
		return toPath(points, false);
	};
	const [sx, sy] = at(0);
	const [ax, ay] = at(lapDistanceM * 0.005);
	return {
		path: toPath(screen, true),
		corners: corners.map((c) => {
			const [x, y] = at(c.distance_m);
			return { label: `${c.number}${c.letter ?? ''}`, x: round(x), y: round(y) };
		}),
		// A zone across the line ends "before" it starts: carry it on a lap.
		drs: drsZones.map((z) =>
			segment(z.start_m, z.end_m < z.start_m ? z.end_m + lapDistanceM : z.end_m)
		),
		start: {
			x: round(sx),
			y: round(sy),
			angle: round((Math.atan2(ay - sy, ax - sx) * 180) / Math.PI)
		}
	};
}

/** URL slug for a circuit id ("marina_bay" -> "marina-bay"), and back. */
export const circuitSlug = (id: string) => id.replace(/_/g, '-');
export const circuitIdFromSlug = (slug: string) => slug.replace(/-/g, '_');
