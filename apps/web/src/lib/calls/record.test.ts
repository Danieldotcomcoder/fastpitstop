import { describe, expect, it } from 'vitest';
import undercutReport from '../../../../../python/f1_kernel/reports/undercut_backtest.md?raw';
import rejoinReport from '../../../../../python/f1_kernel/reports/rejoin_backtest.md?raw';
import { HELD_OUT_RECORD, recordCell } from './record';

/** The section under `heading`, up to the next heading of any level. */
function section(report: string, heading: string): string {
	const after = report.split(heading)[1];
	expect(after, `missing "${heading}"`).toBeDefined();
	const lines = (after ?? '').split('\n').slice(1);
	const end = lines.findIndex((line) => line.startsWith('#'));
	return lines.slice(0, end === -1 ? undefined : end).join('\n');
}

function check(report: string, heading: string, kind: 'undercut' | 'rejoin'): void {
	const record = HELD_OUT_RECORD[kind];
	const body = section(report, heading);
	for (const line of record.lines) {
		const row = new RegExp(`\\|\\s*${line.confidence}\\s*\\|\\s*(\\d+)\\s*\\|\\s*([\\d.]+)%`).exec(
			body
		);
		expect(row, `${kind} ${line.confidence}`).not.toBeNull();
		expect(Number(row?.[1])).toBe(line.calls);
		expect(Number(row?.[2])).toBe(line.hitRatePct);
	}
	expect(body.includes('**W5 PASS**')).toBe(record.passed);
	expect(Number(/over (\d+) race\(s\)/.exec(report)?.[1])).toBe(record.races);
	const corpus = report.split('## Corpus')[1]?.split('##')[0] ?? '';
	const seasons = new Set([...corpus.matchAll(/- (\d{4}) R/g)].map((m) => Number(m[1])));
	expect([...seasons]).toEqual([record.season]);
}

describe('HELD_OUT_RECORD', () => {
	it('matches the undercut report, undercut calls only', () => {
		check(undercutReport, '### Undercut calls only', 'undercut');
	});
	it('matches the rejoin report', () => {
		check(rejoinReport, '## W5 ordering check', 'rejoin');
	});
	it('reads as a rate and the calls it rests on', () => {
		expect(recordCell({ confidence: 'high', calls: 28, hitRatePct: 71.4 })).toBe('71.4% of 28');
	});
});
