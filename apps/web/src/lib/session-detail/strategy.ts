/** Pure transform for the session-detail "Strategy" section: stint bars and
 * pit stops derived from `/laps` (no separate stint/pit-stop endpoint exists
 * — `stint_number`/`compound`/`is_pit_in`/`is_pit_out` on the lap frame are
 * already enough to reconstruct both without a new REST route). */
import type { components } from '$lib/api/client';

type LapRow = components['schemas']['LapRowOut'];

export interface StintBar {
	driver: string;
	stintNumber: number;
	compound: string | null;
	startLap: number;
	endLap: number;
	lapCount: number;
}

export interface PitStop {
	driver: string;
	lapNumber: number;
}

export function buildStints(laps: LapRow[], drivers: string[]): StintBar[] {
	const wanted = new Set(drivers);
	const relevant = laps
		.filter((lap) => wanted.has(lap.driver) && lap.stint_number !== null)
		.slice()
		.sort((a, b) => a.driver.localeCompare(b.driver) || a.lap_number - b.lap_number);

	const stints: StintBar[] = [];
	let current: StintBar | null = null;
	for (const lap of relevant) {
		const stintNumber = lap.stint_number as number;
		if (
			current !== null &&
			current.driver === lap.driver &&
			current.stintNumber === stintNumber
		) {
			current.endLap = lap.lap_number;
			current.lapCount += 1;
			if (current.compound === null) current.compound = lap.compound;
			continue;
		}
		current = {
			driver: lap.driver,
			stintNumber,
			compound: lap.compound,
			startLap: lap.lap_number,
			endLap: lap.lap_number,
			lapCount: 1
		};
		stints.push(current);
	}
	return stints;
}

export function buildPitStops(laps: LapRow[], drivers: string[]): PitStop[] {
	const wanted = new Set(drivers);
	return laps
		.filter((lap) => wanted.has(lap.driver) && lap.is_pit_in === true)
		.map((lap) => ({ driver: lap.driver, lapNumber: lap.lap_number }))
		.sort((a, b) => a.driver.localeCompare(b.driver) || a.lapNumber - b.lapNumber);
}

export function maxStintLap(stints: StintBar[]): number {
	let max = 0;
	for (const s of stints) max = Math.max(max, s.endLap);
	return max;
}
