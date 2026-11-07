import { describe, expect, it } from 'vitest';
import type { Envelope } from '$lib/delay-sync';
import { SectorFlagTracker, flaggedStretches } from './sector-flags';

const rc = (payload: Record<string, unknown>, seconds = 0): Envelope => ({
	event_time_utc: new Date(Date.parse('2026-08-23T13:40:00Z') + seconds * 1000).toISOString(),
	kind: 'race_control_message',
	payload: { category: 'Flag', ...payload }
});

const sectorFlag = (flag: string, sector: number) =>
	rc({ flag, scope: 'Sector', sector, message: `${flag} IN TRACK SECTOR ${sector}` });

describe('SectorFlagTracker', () => {
	it('flags a sector until race control clears it', () => {
		const t = new SectorFlagTracker();
		const log = [sectorFlag('YELLOW', 10), sectorFlag('DOUBLE YELLOW', 11)];
		t.update(log);
		expect([...t.flags]).toEqual([
			[10, 'yellow'],
			[11, 'double_yellow']
		]);
		log.push(sectorFlag('CLEAR', 10));
		t.update(log);
		expect([...t.flags]).toEqual([[11, 'double_yellow']]);
	});

	it('reads the sector from the message for events stored before it was kept', () => {
		const t = new SectorFlagTracker();
		t.update([rc({ flag: 'YELLOW', scope: 'Sector', message: 'YELLOW IN TRACK SECTOR 7' })]);
		expect(t.flags.get(7)).toBe('yellow');
	});

	it('ends every sector flag on a track-wide green, the chequered flag or a red flag', () => {
		for (const ending of [
			rc({ flag: 'GREEN', scope: 'Track', message: 'GREEN LIGHT - PIT EXIT OPEN' }),
			rc({ flag: 'CHEQUERED', scope: 'Track', message: 'CHEQUERED FLAG' }),
			rc({ flag: 'RED', scope: 'Track', message: 'RED FLAG' })
		]) {
			const t = new SectorFlagTracker();
			t.update([sectorFlag('YELLOW', 3), ending]);
			expect(t.flags.size).toBe(0);
		}
	});

	it('ignores flags shown to one driver', () => {
		const t = new SectorFlagTracker();
		t.update([rc({ flag: 'BLUE', scope: 'Driver', message: 'WAVED BLUE FLAG FOR CAR 2' })]);
		expect(t.flags.size).toBe(0);
	});
});

describe('flaggedStretches', () => {
	const sectors = [
		{ number: 1, start_m: 990 },
		{ number: 2, start_m: 200 },
		{ number: 3, start_m: 600 }
	];

	it('runs a sector to the next one starts', () => {
		expect(flaggedStretches(new Map([[2, 'yellow']]), sectors, 1000)).toEqual([
			{ from: 0.2, to: 0.6, flag: 'yellow' }
		]);
	});

	it('runs the last sector on to sector 1, which starts just before the line', () => {
		expect(flaggedStretches(new Map([[3, 'double_yellow']]), sectors, 1000)).toEqual([
			{ from: 0.6, to: 0.99, flag: 'double_yellow' }
		]);
	});
});
