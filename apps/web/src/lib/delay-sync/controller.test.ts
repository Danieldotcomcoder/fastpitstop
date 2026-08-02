import { describe, expect, it } from 'vitest';
import { DelaySyncController } from './controller';
import type { Envelope, KernelSnapshotPayload } from './envelope';

const BASE_MS = Date.parse('2026-03-05T14:00:00.000Z');

function domainEnvelope(offsetMs: number, kind = 'lap_completed'): Envelope {
	return {
		event_time_utc: new Date(BASE_MS + offsetMs).toISOString(),
		kind,
		payload: { offsetMs }
	};
}

function kernelSnapshotEnvelope(
	offsetMs: number,
	crossovers: unknown[] = [{ confidence: 'high' }]
): Envelope {
	return {
		event_time_utc: new Date(BASE_MS + offsetMs).toISOString(),
		kind: 'kernel_snapshot',
		payload: { decision_lap: 12, crossovers }
	};
}

describe('DelaySyncController — spoiler-safety gate', () => {
	it('withholds an envelope whose event_time is ahead of the playback clock', () => {
		const controller = new DelaySyncController({ initialDelaySeconds: 30 });
		controller.ingest(domainEnvelope(0));

		// wall now = BASE_MS + 10s, delay 30s -> playback clock = BASE_MS - 20s.
		// The envelope's event_time (BASE_MS) is still 20s in the controller's future.
		const released = controller.tick(BASE_MS + 10_000);
		expect(released).toEqual([]);
		expect(controller.visibleState.get().events).toEqual([]);
		expect(controller.pendingCount).toBe(1);
	});

	it('releases the envelope once the playback clock reaches it', () => {
		const controller = new DelaySyncController({ initialDelaySeconds: 30 });
		controller.ingest(domainEnvelope(0));

		controller.tick(BASE_MS + 10_000); // still hidden
		const released = controller.tick(BASE_MS + 30_000); // clock = BASE_MS, exactly due

		expect(released).toHaveLength(1);
		expect(controller.visibleState.get().events).toHaveLength(1);
		expect(controller.pendingCount).toBe(0);
	});

	// The scenario the prompt calls out explicitly: a high-confidence
	// undercut snapshot must not leak early just because it's "interesting".
	it('does not release a high-confidence kernel_snapshot ahead of its event_time', () => {
		const controller = new DelaySyncController({ initialDelaySeconds: 60 });
		const snapshot = kernelSnapshotEnvelope(120_000, [
			{ confidence: 'high', reasons: ['undercut opens in 3 laps'] }
		]);
		controller.ingest(snapshot);

		// wall now = BASE_MS + 100s, delay 60s -> playback clock = BASE_MS + 40s.
		// The snapshot's event_time (BASE_MS + 120s) is 80s further out — must stay hidden.
		const released = controller.tick(BASE_MS + 100_000);
		expect(released).toEqual([]);
		expect(controller.visibleState.get().latestSnapshot).toBeNull();

		// Still hidden right up to (but not past) the boundary.
		expect(controller.tick(BASE_MS + 179_000)).toEqual([]);
		expect(controller.visibleState.get().latestSnapshot).toBeNull();

		// Only once wall_now - delay >= the snapshot's own event_time does it appear.
		const releasedAtBoundary = controller.tick(BASE_MS + 180_000);
		expect(releasedAtBoundary).toHaveLength(1);
		const latest = controller.visibleState.get().latestSnapshot as KernelSnapshotPayload;
		expect(latest.decision_lap).toBe(12);
		expect(latest.crossovers).toHaveLength(1);
	});

	it('a lower delay releases the same snapshot earlier — the gate tracks the current delay, not a fixed one', () => {
		const controller = new DelaySyncController({ initialDelaySeconds: 5 });
		controller.ingest(kernelSnapshotEnvelope(10_000));

		// delay 5s -> playback clock at wall_now=BASE_MS+9s is BASE_MS+4s: still hidden.
		expect(controller.tick(BASE_MS + 9_000)).toEqual([]);
		// at wall_now=BASE_MS+15s, playback clock is BASE_MS+10s: due.
		expect(controller.tick(BASE_MS + 15_000)).toHaveLength(1);
	});
});

describe('DelaySyncController — calibration', () => {
	it('setDelaySeconds clamps into [min, max] and marks the state synced', () => {
		const controller = new DelaySyncController({ minDelaySeconds: 0, maxDelaySeconds: 120 });
		expect(controller.delayState.get()).toEqual({ delaySeconds: 0, synced: false });

		controller.setDelaySeconds(999);
		expect(controller.delayState.get()).toEqual({ delaySeconds: 120, synced: true });

		controller.setDelaySeconds(-40);
		expect(controller.delayState.get()).toEqual({ delaySeconds: 0, synced: true });
	});

	it('calibrateFromTap derives and applies the delay from a simulated tap', () => {
		const controller = new DelaySyncController({ minDelaySeconds: 0, maxDelaySeconds: 120 });
		const lightsOutUtc = '2026-03-05T14:00:00.000Z';
		const tapWallMs = Date.parse(lightsOutUtc) + 47_000;

		const applied = controller.calibrateFromTap(tapWallMs, lightsOutUtc);

		expect(applied).toBeCloseTo(47, 9);
		expect(controller.delayState.get()).toEqual({ delaySeconds: applied, synced: true });
	});

	it('clamps a tap-derived delay that falls outside the configured range', () => {
		const controller = new DelaySyncController({ minDelaySeconds: 0, maxDelaySeconds: 120 });
		const eventUtc = '2026-03-05T14:00:00.000Z';
		const tapWallMs = Date.parse(eventUtc) + 500_000; // way more than 120s

		const applied = controller.calibrateFromTap(tapWallMs, eventUtc);
		expect(applied).toBe(120);
	});
});

describe('DelaySyncController — determinism', () => {
	it('the same ingest sequence + fixed clock ticks always produces the same visible state', () => {
		const offsets = [0, 5_000, 15_000, 45_000, 90_000];
		const clockTicks = [BASE_MS + 10_000, BASE_MS + 40_000, BASE_MS + 100_000];

		function run(): unknown {
			const controller = new DelaySyncController({ initialDelaySeconds: 20 });
			for (const offset of offsets) controller.ingest(domainEnvelope(offset));
			for (const clockMs of clockTicks) controller.tick(clockMs);
			return controller.visibleState.get();
		}

		expect(run()).toEqual(run());
	});
});
