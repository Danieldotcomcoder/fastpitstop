/** Sector/mini-sector dominance: which driver (and their current compound)
 * currently holds the fastest measured time through each of N equal
 * arc-length segments of the outline — the "purple sector" idea generalized
 * from the feed's coarse 3-sector split to as many segments as the outline
 * supports, since our domain events carry no spatial sector-boundary data
 * (`SectorCompleted` is a time split, not a place on track). Built entirely
 * from consecutive `car_position` samples projected onto the outline
 * (`arc-length.ts`) — no telemetry/speed channel required, so it works
 * anywhere position data does.
 *
 * A segment's record is replaced only by a STRICTLY faster transit (session
 * "purple" semantics, not "whoever went through most recently") — matching
 * how real broadcast mini-sector coloring works. */
import { type ArcLengthTable, projectToArcLength } from './arc-length';

export interface SegmentBest {
	driver: string | null;
	compound: string | null;
	timeMs: number | null;
}

interface DriverProgress {
	/** Raw `fraction * segmentCount` of the most recent sample, in [0, segmentCount). */
	rawSegments: number;
	/** Unwrapped, monotonically non-decreasing version of the above — keeps
	 * accumulating across a lap completion instead of resetting to 0. */
	cumulativeSegments: number;
	tsMs: number;
}

export const DEFAULT_SEGMENT_COUNT = 24;

export class DominanceTracker {
	readonly segmentCount: number;
	private readonly segmentBest: SegmentBest[];
	private readonly progress = new Map<string, DriverProgress>();
	private readonly lastCrossingMs = new Map<string, number>();

	constructor(
		private readonly table: ArcLengthTable,
		segmentCount: number = DEFAULT_SEGMENT_COUNT
	) {
		this.segmentCount = segmentCount;
		this.segmentBest = Array.from({ length: segmentCount }, () => ({
			driver: null,
			compound: null,
			timeMs: null
		}));
	}

	get segments(): readonly SegmentBest[] {
		return this.segmentBest;
	}

	reset(): void {
		this.progress.clear();
		this.lastCrossingMs.clear();
		for (const segment of this.segmentBest) {
			segment.driver = null;
			segment.compound = null;
			segment.timeMs = null;
		}
	}

	/** Feed one driver's new position sample. `compound` is that driver's
	 * CURRENT tyre compound, if known — attached to any segment record this
	 * sample sets. Samples must arrive in non-decreasing `tsMs` order per
	 * driver (the same order `VisibleSessionState.events` already guarantees);
	 * an out-of-order sample is ignored rather than corrupting a transit time. */
	ingest(driver: string, x: number, y: number, tsMs: number, compound: string | null = null): void {
		const fraction = projectToArcLength(this.table, x, y);
		const rawSegments = fraction * this.segmentCount;
		const previous = this.progress.get(driver);
		if (previous === undefined) {
			this.progress.set(driver, { rawSegments, cumulativeSegments: rawSegments, tsMs });
			return;
		}
		if (tsMs <= previous.tsMs) return;

		let delta = rawSegments - previous.rawSegments;
		if (delta < -this.segmentCount / 2) delta += this.segmentCount;
		if (delta < 0) delta = 0;
		const cumulativeSegments = previous.cumulativeSegments + delta;

		const firstBoundary = Math.floor(previous.cumulativeSegments) + 1;
		const lastBoundary = Math.floor(cumulativeSegments);
		for (let boundary = firstBoundary; boundary <= lastBoundary; boundary++) {
			const boundaryT = delta <= 0 ? 0 : (boundary - previous.cumulativeSegments) / delta;
			const crossingMs = previous.tsMs + (tsMs - previous.tsMs) * boundaryT;
			this._recordCrossing(driver, boundary, crossingMs, compound);
		}

		this.progress.set(driver, { rawSegments, cumulativeSegments, tsMs });
	}

	private _recordCrossing(
		driver: string,
		boundary: number,
		crossingMs: number,
		compound: string | null
	): void {
		const priorCrossingMs = this.lastCrossingMs.get(driver);
		this.lastCrossingMs.set(driver, crossingMs);
		if (priorCrossingMs === undefined) return;

		const transitMs = crossingMs - priorCrossingMs;
		if (transitMs <= 0) return;
		const segmentIndex = (((boundary - 1) % this.segmentCount) + this.segmentCount) % this.segmentCount;
		const best = this.segmentBest[segmentIndex];
		if (best === undefined) return; // segmentIndex is always < segmentBest.length by construction
		if (best.timeMs === null || transitMs < best.timeMs) {
			best.driver = driver;
			best.compound = compound;
			best.timeMs = transitMs;
		}
	}
}
