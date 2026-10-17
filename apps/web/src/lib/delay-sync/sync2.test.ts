/** Sync 2.0 (ROADMAP W6): pause and resume, re-sync on the leader crossing
 * the line, catch-up from lights out, carrying a sync across a reconnect,
 * and the sync quality verdict. Pure logic, no DOM. */
import { describe, expect, it } from 'vitest';
import { DelaySyncController } from './controller';
import type { Envelope } from './envelope';
import { FEED_BEHIND_MS, SYNC_FRESH_MS, syncQuality } from './quality';
import { leaderCrossings, nearestCrossing } from './resync';

const BASE_MS = Date.parse('2026-10-25T19:00:00.000Z');

function at(
	offsetMs: number,
	kind = 'gap_updated',
	payload: Record<string, unknown> = {}
): Envelope {
	return { event_time_utc: new Date(BASE_MS + offsetMs).toISOString(), kind, payload };
}
const crossing = (offsetMs: number, lap: number) =>
	at(offsetMs, 'lap_completed', { lap_number: lap });
const ms = (e: Envelope) => Date.parse(e.event_time_utc);

/** Seeded, so a failure reproduces. */
function rng(seed: number): () => number {
	let s = seed >>> 0;
	return () => {
		s = (s + 0x6d2b79f5) >>> 0;
		let t = s;
		t = Math.imul(t ^ (t >>> 15), t | 1);
		t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

describe('pause and resume', () => {
	it('freezes the clock, then carries on from exactly where it stopped', () => {
		const c = new DelaySyncController({ initialDelaySeconds: 10 });
		c.ingest(at(0));
		c.ingest(at(5_000));
		c.ingest(at(40_000));
		c.tick(BASE_MS + 12_000); // clock = BASE + 2 s
		c.pause(BASE_MS + 12_000);
		const frozen = c.clockMs(BASE_MS + 12_000);
		// A long pause: nothing past the frozen clock is released.
		expect(c.tick(BASE_MS + 90_000)).toEqual([]);
		expect(c.clockMs(BASE_MS + 90_000)).toBe(frozen);
		expect(c.delayState.get().paused).toBe(true);

		c.resume(BASE_MS + 100_000);
		expect(c.clockMs(BASE_MS + 100_000)).toBe(frozen);
		expect(c.delayState.get()).toMatchObject({ paused: false, delaySeconds: 98 });
		expect(c.tick(BASE_MS + 103_000).map(ms)).toEqual([BASE_MS + 5_000]);
	});

	it('ignores a second pause and a resume while running', () => {
		const c = new DelaySyncController({ initialDelaySeconds: 5 });
		c.pause(BASE_MS);
		c.pause(BASE_MS + 30_000);
		c.resume(BASE_MS + 40_000);
		expect(c.delayState.get().delaySeconds).toBe(45);
		c.resume(BASE_MS + 90_000);
		expect(c.delayState.get().delaySeconds).toBe(45);
	});

	it('never releases ahead of the viewer, over 200 random schedules', () => {
		for (let trial = 0; trial < 200; trial++) {
			const random = rng(trial + 1);
			const c = new DelaySyncController({
				initialDelaySeconds: Math.floor(random() * 60),
				now: () => BASE_MS
			});
			// An independent model of where the viewer's TV is.
			let delayMs = c.delayState.get().delaySeconds * 1000;
			let pausedAt: number | null = null;
			const model = (wall: number) => (pausedAt ?? wall) - delayMs;

			const count = 50 + Math.floor(random() * 300);
			for (let i = 0; i < count; i++) {
				const offset = Math.floor(random() * 600_000);
				c.ingest(random() < 0.1 ? crossing(offset, 1 + Math.floor(offset / 90_000)) : at(offset));
			}
			let wall = BASE_MS;
			let lastClock = -Infinity;
			let wasPaused = false;
			for (let step = 0; step < 120; step++) {
				wall += Math.floor(random() * 20_000);
				const roll = random();
				if (roll < 0.12) {
					c.pause(wall);
					pausedAt ??= wall;
				} else if (roll < 0.24) {
					c.resume(wall);
					if (pausedAt !== null) {
						delayMs += wall - pausedAt;
						pausedAt = null;
					}
				} else if (roll < 0.3) {
					c.setDelaySeconds(Math.floor(random() * 120), wall);
					delayMs = c.delayState.get().delaySeconds * 1000;
				} else if (roll < 0.34) {
					if (c.resyncOnLeader(wall) !== null) delayMs = c.delayState.get().delaySeconds * 1000;
				}
				expect(c.clockMs(wall)).toBe(model(wall));
				for (const released of c.tick(wall)) expect(ms(released)).toBeLessThanOrEqual(model(wall));
				// While paused, a plain tick leaves the clock exactly where it was:
				// only the viewer (a slider, a tap) moves a paused clock.
				if (roll >= 0.34 && pausedAt !== null && wasPaused) expect(c.clockMs(wall)).toBe(lastClock);
				lastClock = c.clockMs(wall);
				wasPaused = pausedAt !== null;
			}
		}
	});
});

describe('carrying a sync over', () => {
	it('keeps the delay, how it was set, and a pause in progress', () => {
		const a = new DelaySyncController({ initialDelaySeconds: 8 });
		a.setDelaySeconds(33, BASE_MS);
		a.pause(BASE_MS + 1_000);
		const b = new DelaySyncController({ carry: a.carry() });
		expect(b.delayState.get()).toMatchObject({
			delaySeconds: 33,
			method: 'slider',
			paused: true,
			setAtMs: BASE_MS,
			synced: true
		});
		expect(b.clockMs(BASE_MS + 50_000)).toBe(a.clockMs(BASE_MS + 50_000));
		b.resume(BASE_MS + 11_000);
		expect(b.delayState.get().delaySeconds).toBe(43);
	});

	it('keeps a catch-up delay far beyond the slider', () => {
		const a = new DelaySyncController();
		a.catchUpFrom(BASE_MS + 3_600_000, BASE_MS);
		const b = new DelaySyncController({ carry: a.carry() });
		expect(b.delayState.get()).toMatchObject({ delaySeconds: 3600, method: 'catch-up' });
	});
});

describe('catch-up and taps', () => {
	it('starts from lights out with no slider cap; the lights out tap keeps it', () => {
		const c = new DelaySyncController();
		expect(c.catchUpFrom(BASE_MS + 5_400_000, BASE_MS)).toBe(5400);
		expect(c.delayState.get()).toMatchObject({ method: 'catch-up', setAtMs: BASE_MS + 5_400_000 });
		expect(c.calibrateFromTap(BASE_MS + 5_400_000, BASE_MS)).toBe(120);
		expect(c.delayState.get().method).toBe('lights-out');
	});
});

describe('re-sync on the leader crossing the line', () => {
	it('takes the first car to complete each lap as the leader', () => {
		const crossings = leaderCrossings([
			crossing(95_000, 1),
			crossing(93_000, 1),
			at(94_000),
			crossing(185_000, 2),
			crossing(190_000, 1)
		]);
		expect(crossings).toEqual([
			{ lap: 1, eventMs: BASE_MS + 93_000 },
			{ lap: 2, eventMs: BASE_MS + 185_000 }
		]);
	});

	it('picks the crossing nearest the current delay, within bounds', () => {
		const crossings = [
			{ lap: 10, eventMs: BASE_MS },
			{ lap: 11, eventMs: BASE_MS + 90_000 },
			{ lap: 12, eventMs: BASE_MS + 180_000 }
		];
		// Tapped at +130 s with a 30 s delay: lap 11 (40 s) beats lap 10 (130 s).
		expect(nearestCrossing(crossings, BASE_MS + 130_000, 30, { min: 0, max: 120 })).toEqual({
			lap: 11,
			eventMs: BASE_MS + 90_000,
			delaySeconds: 40
		});
		// A crossing still ahead of the tap would need a negative delay.
		expect(nearestCrossing(crossings, BASE_MS + 10_000, 0, { min: 0, max: 120 })?.lap).toBe(10);
		expect(nearestCrossing([], BASE_MS, 0, { min: 0, max: 120 })).toBeNull();
	});

	it('sets the delay from the stream it has received, released or not', () => {
		const c = new DelaySyncController({ initialDelaySeconds: 30 });
		c.ingest(crossing(0, 10));
		c.ingest(crossing(90_000, 11));
		c.ingest(crossing(91_000, 11));
		expect(c.hasLeaderCrossings).toBe(true);
		const picked = c.resyncOnLeader(BASE_MS + 132_500);
		expect(picked).toMatchObject({ lap: 11, delaySeconds: 42.5 });
		expect(c.delayState.get()).toMatchObject({
			delaySeconds: 42.5,
			method: 'leader',
			anchorLap: 11,
			setAtMs: BASE_MS + 132_500
		});
		expect(new DelaySyncController().resyncOnLeader(BASE_MS)).toBeNull();
	});
});

describe('newest delivered', () => {
	it('is the latest event time ingested, released or not', () => {
		const c = new DelaySyncController({ initialDelaySeconds: 0 });
		expect(c.newestReceivedMs).toBeNull();
		c.ingest(at(5_000));
		c.ingest(at(1_000));
		c.tick(BASE_MS + 10_000);
		expect(c.newestReceivedMs).toBe(BASE_MS + 5_000);
	});
});

describe('syncQuality', () => {
	const fresh = new DelaySyncController({ initialDelaySeconds: 0 }).delayState.get();
	const base = { nowMs: BASE_MS, clockMs: BASE_MS, newestReceivedMs: BASE_MS, isLive: true };

	it('says when nothing has been lined up yet', () => {
		expect(syncQuality({ ...base, delay: fresh }).level).toBe('unsynced');
	});
	it('trusts a fresh tap, and asks for another once it has aged', () => {
		const tapped = {
			...fresh,
			method: 'leader' as const,
			setAtMs: BASE_MS,
			anchorLap: 23,
			synced: true
		};
		const now = syncQuality({ ...base, delay: tapped });
		expect(now.level).toBe('good');
		expect(now.detail).toContain('lap 23');
		const later = syncQuality({ ...base, delay: tapped, nowMs: BASE_MS + SYNC_FRESH_MS + 60_000 });
		expect(later.level).toBe('check');
	});
	it('marks a hand-set delay as worth checking', () => {
		const slid = { ...fresh, method: 'slider' as const, setAtMs: BASE_MS, synced: true };
		expect(syncQuality({ ...base, delay: slid }).level).toBe('check');
	});
	it('says so while paused, before anything else', () => {
		expect(syncQuality({ ...base, delay: { ...fresh, paused: true } }).level).toBe('paused');
	});
	it('warns when a live feed has delivered nothing near the clock', () => {
		const quiet = { ...base, delay: fresh, newestReceivedMs: BASE_MS - FEED_BEHIND_MS - 1 };
		expect(syncQuality(quiet).level).toBe('behind');
		expect(syncQuality({ ...quiet, isLive: false }).level).toBe('unsynced');
	});
});
