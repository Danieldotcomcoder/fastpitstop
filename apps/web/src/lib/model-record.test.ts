import { describe, expect, it } from 'vitest';
import report from '../../../../python/f1_kernel/reports/undercut_backtest.md?raw';
import { MODEL_RECORD } from './model-record';

describe('MODEL_RECORD', () => {
	it('matches the committed held-out undercut backtest', () => {
		const high = /\|\s*high\s*\|\s*(\d+)\s*\|\s*([\d.]+)%\s*\|/.exec(report);
		expect(high).not.toBeNull();
		expect(Number(high?.[1])).toBe(MODEL_RECORD.highConfidenceCalls);
		expect(Number(high?.[2])).toBe(MODEL_RECORD.highConfidenceHitRatePct);
		const races = /over (\d+) race\(s\)/.exec(report);
		expect(Number(races?.[1])).toBe(MODEL_RECORD.races);
		const corpus = report.split('## Corpus')[1]?.split('##')[0] ?? '';
		const seasons = new Set([...corpus.matchAll(/- (\d{4}) R/g)].map((m) => Number(m[1])));
		expect([...seasons]).toEqual([MODEL_RECORD.season]);
	});
});
