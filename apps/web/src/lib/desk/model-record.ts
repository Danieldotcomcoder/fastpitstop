/** The undercut model's published record, quoted beside its calls (mockup
 * A's "What could happen next"). Copied from the committed held-out
 * backtest, `python/f1_kernel/reports/undercut_backtest.md`, and checked
 * against it by `model-record.test.ts`, so the two cannot drift apart.
 * This is the model's record on races it never saw, not this race's
 * scorecard: a live per-race scorecard is W5 (M4), not built yet. */
export const MODEL_RECORD = {
	season: 2025,
	races: 10,
	highConfidenceCalls: 70,
	highConfidenceHitRatePct: 85.7
} as const;

export function modelRecordLine(): string {
	const r = MODEL_RECORD;
	return `On ${r.races} races of ${r.season} the model never saw, its high-confidence calls were right ${r.highConfidenceHitRatePct}% of the time (${r.highConfidenceCalls} calls).`;
}
