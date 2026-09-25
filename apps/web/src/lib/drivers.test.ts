import { describe, expect, it } from 'vitest';
import { driverName, lapTime } from './drivers';
import { namesFromEvents } from './driver-events';
import { DelaySyncController } from './delay-sync';
import { TimingTowerReducer } from './timing-tower';
describe('session identities', () => {
	it('uses the session code, never a fixed racing-number roster', () => {
		expect(driverName('1', null, 'VER')).toBe('Max Verstappen');
		expect(driverName('1', null, 'NOR')).toBe('Lando Norris');
		expect(driverName('3', null, 'RIC')).toBe('Daniel Ricciardo');
		expect(driverName('3', null, 'VER')).toBe('Max Verstappen');
		expect(driverName('41', 'New Reserve Driver', 'NEW')).toBe('New Reserve Driver');
		expect(driverName('99')).toBe('Driver 99');
	});
	it('releases names with the driver list, retains them through sparse updates, resets for a new session', () => {
		const c = new DelaySyncController({ initialDelaySeconds: 0 });
		c.ingest({
			event_time_utc: new Date(2000).toISOString(),
			kind: 'driver_list_updated',
			payload: { drivers: [{ number: '1', code: 'NOR', full_name: 'Lando Norris' }] }
		});
		c.tick(1999);
		expect(namesFromEvents(c.visibleState.get().events)).toEqual({});
		c.tick(2000);
		const reducer = new TimingTowerReducer();
		reducer.update(c.visibleState.get().events);
		expect(reducer.rows[0]?.name).toBe('Lando Norris');
		c.ingest({
			event_time_utc: new Date(2001).toISOString(),
			kind: 'driver_list_updated',
			payload: { drivers: [{ number: '1', team: 'McLaren' }] }
		});
		c.tick(2001);
		reducer.update(c.visibleState.get().events);
		expect(reducer.rows[0]?.name).toBe('Lando Norris');
		expect(namesFromEvents(c.visibleState.get().events)['1']).toBe('Lando Norris');
		reducer.reset();
		expect(reducer.rows).toEqual([]);
	});
	it('resolves number-only legacy API responses by season', () => {
		expect(driverName('1', null, null, 2024)).toBe('Max Verstappen');
		expect(driverName('1', null, null, 2026)).toBe('Lando Norris');
		expect(driverName('3', null, null, 2024)).toBe('Daniel Ricciardo');
		expect(driverName('3', null, null, 2026)).toBe('Max Verstappen');
		expect(driverName('2', null, null, 2024)).toBe('Logan Sargeant');
		expect(driverName('41', null, null, 2026)).toBe('Arvid Lindblad');
		expect(driverName('1', null, 'NEW', 2026)).toBe('NEW');
		expect(driverName('1', null, null, 2027)).toBe('Driver 1');
	});
	it('formats lap times with minute rollover', () => {
		expect(lapTime(89.123)).toBe('1:29.123');
		expect(lapTime(59.9999)).toBe('1:00.000');
		expect(lapTime(null)).toBe('—');
	});
});
