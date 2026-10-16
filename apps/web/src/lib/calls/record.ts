/** Each call kind's record on 2025 races the model never saw (ROADMAP W5):
 * the held-out backtests, `python/f1_kernel/reports/undercut_backtest.md`
 * ("Undercut calls only", the calls the desk shows) and
 * `rejoin_backtest.md`. Copied, and checked against those reports by
 * `record.test.ts`, so the two cannot drift apart.
 *
 * `passed` is the report's own W5 verdict (high beats medium beats low). A
 * kind is shown and counted on the desk only once it has passed and
 * `f1_kernel.CLEARED_CALL_KINDS` lists it. */
import type { CallConfidence, CallKind } from './view';

export interface RecordLine {
	confidence: CallConfidence;
	calls: number;
	hitRatePct: number;
}

export interface KindRecord {
	kind: CallKind;
	season: number;
	races: number;
	passed: boolean;
	lines: readonly RecordLine[];
}

export const HELD_OUT_RECORD: Readonly<Record<CallKind, KindRecord>> = {
	undercut: {
		kind: 'undercut',
		season: 2025,
		races: 10,
		passed: false,
		lines: [
			{ confidence: 'high', calls: 28, hitRatePct: 71.4 },
			{ confidence: 'medium', calls: 21, hitRatePct: 66.7 },
			{ confidence: 'low', calls: 42, hitRatePct: 66.7 }
		]
	},
	rejoin: {
		kind: 'rejoin',
		season: 2025,
		races: 10,
		passed: false,
		lines: [
			{ confidence: 'high', calls: 33, hitRatePct: 87.9 },
			{ confidence: 'medium', calls: 9, hitRatePct: 22.2 },
			{ confidence: 'low', calls: 49, hitRatePct: 51.0 }
		]
	}
};

/** "71.4% of 28". */
export function recordCell(line: RecordLine): string {
	return `${line.hitRatePct.toFixed(1)}% of ${line.calls}`;
}
