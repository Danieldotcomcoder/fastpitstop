/** The phone's Race tab (ROADMAP W7, direction A): the race in three
 * seconds. Pure helpers over what the desk already derives from Delay
 * Sync's released state, so the glance can never show more than the full
 * desk does. */

import type { BoardModel } from './pit-board';
import type { SparkPoint } from './snapshot-history';

/** How far back the battle's trend is read: long enough to show a gap
 * closing, short enough to be this stint's story. */
export const TREND_LAPS = 8;

/** Gaps within this of each other read as holding steady. */
const STEADY_S = 0.1;

export interface GapTrend {
	nowS: number;
	fromS: number;
	fromLap: number;
	direction: 'down' | 'up' | 'steady';
}

/** The focused battle's gap now against where it was up to `TREND_LAPS`
 * laps ago. Null with fewer than two measured laps. */
export function gapTrend(spark: readonly SparkPoint[]): GapTrend | null {
	const measured = spark.filter((p): p is SparkPoint & { gapS: number } => p.gapS !== null);
	const last = measured[measured.length - 1];
	if (!last) return null;
	const first = measured.find((p) => p.lap >= last.lap - TREND_LAPS);
	if (!first || first === last) return null;
	const change = last.gapS - first.gapS;
	return {
		nowS: last.gapS,
		fromS: first.gapS,
		fromLap: first.lap,
		direction: Math.abs(change) < STEADY_S ? 'steady' : change < 0 ? 'down' : 'up'
	};
}

/** One line under the battle: how the gap moved, then the tyres. */
export function battleLine(trend: GapTrend | null, model: BoardModel): string {
	const parts: string[] = [];
	if (trend) {
		const from = trend.fromS.toFixed(1);
		parts.push(
			trend.direction === 'steady'
				? `Holding at about ${trend.nowS.toFixed(1)} s since lap ${trend.fromLap}.`
				: `${trend.direction === 'down' ? 'Down' : 'Up'} from ${from} s on lap ${trend.fromLap}.`
		);
	}
	const a = model.attacker;
	const b = model.defender;
	if (a && b && a.compound && a.compound === b.compound) {
		const sameAge =
			a.tyreAge !== null && b.tyreAge !== null && Math.abs(a.tyreAge - b.tyreAge) <= 1;
		parts.push(sameAge ? 'Same tyres, same age.' : 'Same tyres.');
	}
	return parts.join(' ');
}
