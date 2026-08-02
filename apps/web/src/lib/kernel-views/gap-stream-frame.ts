/** Turns accumulated `GapStreamState` into the plain, already-resolved
 * shape the Canvas renderer draws — matching the `frame.ts` / `renderer.ts`
 * split `$lib/track-map` uses, so team-color lookup and "what counts as a
 * plottable point" live here, not in drawing code. */
import { colorForTeam } from '$lib/track-map';
import type { GapStreamState } from './gap-stream';

export interface GapStreamPoint {
	lap: number;
	gapS: number;
	tsMs: number;
}

export interface GapStreamBand {
	driver: string;
	code: string | null;
	color: string;
	points: GapStreamPoint[];
}

export interface GapStreamFrame {
	bands: GapStreamBand[];
	maxLap: number;
	maxGapS: number;
}

export function buildGapStreamFrame(state: GapStreamState): GapStreamFrame {
	const bands: GapStreamBand[] = [];
	let maxLap = 0;
	let maxGapS = 0;

	for (const series of state.drivers.values()) {
		const points: GapStreamPoint[] = [];
		for (const sample of series.samples) {
			// A lapped or unknown gap breaks the flowing line rather than
			// plotting a fabricated value — the same "never invent a number"
			// discipline the kernel itself follows.
			if (sample.gapToLeaderS === null || sample.lapNumber === null) continue;
			points.push({ lap: sample.lapNumber, gapS: sample.gapToLeaderS, tsMs: sample.tsMs });
			if (sample.lapNumber > maxLap) maxLap = sample.lapNumber;
			if (sample.gapToLeaderS > maxGapS) maxGapS = sample.gapToLeaderS;
		}
		if (points.length === 0) continue;
		bands.push({
			driver: series.driver,
			code: series.code,
			color: colorForTeam(series.team),
			points
		});
	}

	return { bands, maxLap, maxGapS };
}
