/** Which battle the desk is watching. The design's rule: the focused
 * driver attacks the car directly ahead; if the focused driver leads, the
 * car in second attacks them. With no focus chosen yet, the desk follows
 * the fight for the lead. */
import type { DriverRow } from '$lib/timing-tower';

export interface FocusPair {
	/** The attacker: the car behind. */
	a: string;
	/** The defender: the car ahead. */
	b: string;
}

export function focusPair(rows: readonly DriverRow[], focus: string | null): FocusPair | null {
	const placed = rows
		.filter((row): row is DriverRow & { position: number } => row.position !== null)
		.sort((x, y) => x.position - y.position);
	if (placed.length < 2) return null;
	const focused = (focus !== null && placed.find((row) => row.driver === focus)) || placed[1];
	if (focused === undefined) return null;
	const index = placed.indexOf(focused);
	if (index === 0) {
		const second = placed[1];
		return second ? { a: second.driver, b: focused.driver } : null;
	}
	const ahead = placed[index - 1];
	return ahead ? { a: focused.driver, b: ahead.driver } : null;
}
