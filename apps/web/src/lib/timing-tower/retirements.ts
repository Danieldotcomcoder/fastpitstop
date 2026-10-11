/** Which cars are out of the race, judged from the released tower rows
 * only (each driver's last completed lap, position and laps down), so a
 * retirement can never show before the viewer has seen it, live or in a
 * replay.
 *
 * The rule follows the story engine's (`f1_why.detect._detect_retirements`)
 * as it judges a race still running: a car is out once any car that was
 * running behind it has gone TWO laps further. One lap isn't enough live: a
 * car passed by the whole field during a long stop has also not completed
 * the lap they have (the 2024 Miami GP's Piastri, on his out-lap after a
 * front-wing change, flickered "out"), and no stop costs a whole extra lap.
 * "Any", not "every", and cars already found out are no witnesses: judged
 * again until nothing changes, so a pile-up resolves from the back (the
 * 2024 Monaco GP's lap-1 crash: Magnussen, then Hülkenberg, then Pérez,
 * whose only cars behind had stopped too).
 *
 * The last car on the road, with nobody running behind, is out once the
 * leader is two laps further than its own laps down allow: a car two laps
 * down is three behind every time the leader crosses the line (2024 Monaco's
 * Sargeant and Zhou flickered "out" with a flat three).
 *
 * A car that later completes another lap (a long repair) is simply back.
 * The feed's own retirement flag isn't in our event stream, which is why
 * this is judged from the laps. */
import type { DriverRow } from './types';

/** Laps further a car behind (or, for the last car, the leader beyond its
 * laps down) must be: one more than any stop can cost. */
const LAPS_ON = 2;

export function retiredDrivers(rows: readonly DriverRow[]): Set<string> {
	const out = new Set<string>();
	const started = rows.some((r) => (r.lapNumber ?? 0) > 0);
	if (!started) return out;
	const mostLaps = Math.max(...rows.map((r) => r.lapNumber ?? 0));
	const placed = (row: DriverRow) => row.position ?? row.startPosition;
	let changed = true;
	while (changed) {
		changed = false;
		for (const row of rows) {
			const position = placed(row);
			if (out.has(row.driver) || position === null) continue;
			const last = row.lapNumber ?? 0;
			// Cars still running behind this one, slower or lapped ones too: a
			// car one lap behind is no reason to call this one the last on the
			// road (the 2024 São Paulo GP's Colapinto, with Hülkenberg a lap
			// back, flickered "out"). Cars that stopped earlier are already
			// in `out` by the time they would matter.
			const behind = rows.filter(
				(other) =>
					other.driver !== row.driver && !out.has(other.driver) && (placed(other) ?? 0) > position
			);
			const gone =
				behind.length > 0
					? behind.some((other) => (other.lapNumber ?? 0) >= last + LAPS_ON)
					: mostLaps >= last + (row.lapsDown ?? 0) + LAPS_ON;
			if (gone) {
				out.add(row.driver);
				changed = true;
			}
		}
	}
	return out;
}
