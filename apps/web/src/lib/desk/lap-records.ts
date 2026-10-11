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
	/** Per sector (index 0-2): the session's best, and each driver's own. */
	private readonly sectorSessionBest: (number | null)[] = [null, null, null];
	private readonly sectorBest = new Map<string, (number | null)[]>();
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		if (events.length < this.cursor) this.reset();
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope?.kind !== 'lap_completed') continue;
			const driver = envelope.payload.driver;
			if (typeof driver === 'string') this.foldSectors(driver, envelope.payload.sector_times);
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

	private foldSectors(driver: string, raw: unknown): void {
		if (!Array.isArray(raw)) return;
		const own = this.sectorBest.get(driver) ?? [null, null, null];
		raw.slice(0, 3).forEach((value, index) => {
			const time = parseIsoDurationSeconds(value);
			if (time === null || time <= 0) return;
			const mine = own[index];
			if (mine === null || mine === undefined || time < mine) own[index] = time;
			const session = this.sectorSessionBest[index];
			if (session === null || session === undefined || time < session)
				this.sectorSessionBest[index] = time;
		});
		this.sectorBest.set(driver, own);
	}

	/** The same purple/green rule for one sector of a lap (index 0-2). */
	classifySector(driver: string, index: number, time: number | null): '' | 'fastest' | 'pb' {
		if (time === null) return '';
		if (this.sectorSessionBest[index] === time) return 'fastest';
		if (this.sectorBest.get(driver)?.[index] === time) return 'pb';
		return '';
	}

	reset(): void {
		this.best.clear();
		this.sessionBest = null;
		this.sectorSessionBest.fill(null);
		this.sectorBest.clear();
		this.cursor = 0;
	}
}
