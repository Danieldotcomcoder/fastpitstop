/** Best laps from the released event log: each driver's personal best and
 * the session's fastest lap so far. The timing tower colours a last lap
 * purple when it IS the session best and green when it is that driver's
 * personal best, exactly as a broadcast timing screen does.
 *
 * Folded only from `lap_completed` envelopes Delay Sync has already
 * released, so a fastest lap set later in the race can never colour a row
 * early. Incremental, like `TimingTowerReducer`: each `update` reads only
 * the envelopes appended since the last one. */
import type { Envelope } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from '$lib/kernel-views/iso-duration';

export class LapRecordReducer {
	private readonly best = new Map<string, number>();
	private sessionBest: number | null = null;
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		if (events.length < this.cursor) this.reset();
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope?.kind !== 'lap_completed') continue;
			const driver = envelope.payload.driver;
			// The stream carries `LapCompleted` verbatim: `lap_time` as an ISO
			// duration, parsed exactly as the timing tower parses it.
			const time = parseIsoDurationSeconds(envelope.payload.lap_time);
			if (typeof driver !== 'string' || time === null || time <= 0) continue;
			const previous = this.best.get(driver);
			if (previous === undefined || time < previous) this.best.set(driver, time);
			if (this.sessionBest === null || time < this.sessionBest) this.sessionBest = time;
		}
		this.cursor = events.length;
	}

	/** `fastest` when `lapTime` equals the session best, `pb` when it equals
	 * this driver's own best, `''` otherwise. */
	classify(driver: string, lapTime: number | null): '' | 'fastest' | 'pb' {
		if (lapTime === null) return '';
		if (this.sessionBest !== null && lapTime === this.sessionBest) return 'fastest';
		if (this.best.get(driver) === lapTime) return 'pb';
		return '';
	}

	reset(): void {
		this.best.clear();
		this.sessionBest = null;
		this.cursor = 0;
	}
}
