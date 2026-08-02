import { describe, expect, it } from 'vitest';
import { buildArcLengthTable } from './arc-length';
import { DominanceTracker } from './dominance';
import type { Point } from './types';

/** Five colinear points at fractions 0, 0.25, 0.5, 0.75, 1.0 — with
 * `segmentCount = 4` every boundary lands exactly on a table vertex, so
 * crossing times are exact rather than approximated. */
function lineTable() {
	const points: Point[] = [
		[0, 0],
		[1, 0],
		[2, 0],
		[3, 0],
		[4, 0]
	];
	return buildArcLengthTable(points);
}

function circleTable(steps = 360): ReturnType<typeof buildArcLengthTable> {
	const points: Point[] = [];
	for (let i = 0; i <= steps; i++) {
		const angle = (2 * Math.PI * i) / steps;
		points.push([Math.cos(angle), Math.sin(angle)]);
	}
	return buildArcLengthTable(points);
}

describe('DominanceTracker', () => {
	it('records no segment time from a single sample (no prior crossing to measure from)', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 0, 'medium');
		expect(tracker.segments.every((s) => s.timeMs === null)).toBe(true);
	});

	it('records a segment transit time once two boundaries have been crossed', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 0, 'medium'); // fraction 0
		tracker.ingest('A', 1, 0, 1000, 'medium'); // fraction .25 -> crosses boundary 1
		tracker.ingest('A', 2, 0, 1800, 'medium'); // fraction .5 -> crosses boundary 2

		const segment1 = tracker.segments[1]!;
		expect(segment1.driver).toBe('A');
		expect(segment1.compound).toBe('medium');
		expect(segment1.timeMs).toBeCloseTo(800, 6);
	});

	it('a strictly faster transit takes over the segment record', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 0, 'medium');
		tracker.ingest('A', 1, 0, 1000, 'medium');
		tracker.ingest('A', 2, 0, 1800, 'medium'); // A's segment 1 time: 800ms

		tracker.ingest('B', 0, 0, 5000, 'soft');
		tracker.ingest('B', 1, 0, 5400, 'soft');
		tracker.ingest('B', 2, 0, 5700, 'soft'); // B's segment 1 time: 300ms

		const segment1 = tracker.segments[1]!;
		expect(segment1.driver).toBe('B');
		expect(segment1.compound).toBe('soft');
		expect(segment1.timeMs).toBeCloseTo(300, 6);
	});

	it('a slower transit never overwrites an existing faster record', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 0);
		tracker.ingest('A', 1, 0, 1000);
		tracker.ingest('A', 2, 0, 1300); // 300ms

		tracker.ingest('C', 0, 0, 9000);
		tracker.ingest('C', 1, 0, 9400);
		tracker.ingest('C', 2, 0, 10300); // 900ms — slower

		expect(tracker.segments[1]!.driver).toBe('A');
		expect(tracker.segments[1]!.timeMs).toBeCloseTo(300, 6);
	});

	it('ignores an out-of-order sample rather than corrupting a transit time', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 1000);
		tracker.ingest('A', 1, 0, 500); // earlier than the previous sample: ignored
		tracker.ingest('A', 2, 0, 2000);

		// With the out-of-order sample ignored, only one real transition (0
		// directly to fraction .5) is on record — no crash, no bogus negative time.
		expect(tracker.segments.some((s) => s.timeMs !== null && s.timeMs < 0)).toBe(false);
	});

	it('reset clears both progress and recorded bests', () => {
		const tracker = new DominanceTracker(lineTable(), 4);
		tracker.ingest('A', 0, 0, 0);
		tracker.ingest('A', 1, 0, 1000);
		tracker.ingest('A', 2, 0, 1800);
		expect(tracker.segments[1]!.timeMs).not.toBeNull();

		tracker.reset();

		expect(tracker.segments.every((s) => s.timeMs === null && s.driver === null)).toBe(true);
		// Progress state was cleared too: a fresh pair of samples must be
		// treated as a new baseline, not a huge jump from stale history.
		tracker.ingest('A', 0, 0, 50_000);
		tracker.ingest('A', 1, 0, 50_100);
		expect(tracker.segments[0]!.timeMs).toBeNull();
	});

	it('crosses the lap boundary (wraparound) without corrupting progress', () => {
		const tracker = new DominanceTracker(circleTable(), 4);
		const fractionPoint = (fraction: number): Point => {
			const angle = fraction * 2 * Math.PI;
			return [Math.cos(angle), Math.sin(angle)];
		};

		tracker.ingest('W', ...fractionPoint(0.1), 0);
		tracker.ingest('W', ...fractionPoint(0.9), 1000); // crosses boundaries 1, 2, 3
		tracker.ingest('W', ...fractionPoint(0.1), 2000); // wraps forward across the line -> boundary 4

		const wrapSegment = tracker.segments[3]!; // segment (4 - 1) % 4 = 3
		expect(wrapSegment.driver).toBe('W');
		expect(wrapSegment.timeMs).not.toBeNull();
		expect(wrapSegment.timeMs as number).toBeGreaterThan(0);
		expect(wrapSegment.timeMs as number).toBeLessThan(1000);
	});
});
