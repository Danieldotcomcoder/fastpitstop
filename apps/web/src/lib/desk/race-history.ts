/** The race so far, lap by lap: every car's position and gap at the end of
 * each lap it has completed, the laps run under a safety car, VSC or red
 * flag, and the laps each car pitted on. It feeds the desk's race ribbon
 * (ROADMAP W2 D3, "How the top ten got here") and the battle card's gap
 * trend.
 *
 * Folded from the released event log only, so it grows as the viewer's
 * clock does and can never hold a lap the viewer has not reached. It needs
 * no REST call, so it grows during a live race too.
 *
 * Neutralised laps come from the feed's own track status when the stream
 * carries it (live sessions), and otherwise from race control's safety-car
 * and red-flag messages: a backfilled race has no track-status stream
 * (DATA_CONTRACTS.md), and `f1_kernel.status` rebuilds it the same way. */
import type { Envelope } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from '$lib/kernel-views';

export type Neutral = 'vsc' | 'sc' | 'red';

const SEVERITY: Record<Neutral, number> = { vsc: 1, sc: 2, red: 3 };

export interface HistoryPoint {
	/** Laps completed: 0 is the grid, before the first lap. */
	lap: number;
	position: number | null;
	/** Seconds behind the leader; null when unknown or lapped. */
	gapS: number | null;
}

export interface NeutralBand {
	fromLap: number;
	/** Inclusive. */
	toLap: number;
	status: Neutral;
}

function worst(statuses: readonly (Neutral | null)[]): Neutral | null {
	let out: Neutral | null = null;
	for (const s of statuses) {
		if (s !== null && (out === null || SEVERITY[s] > SEVERITY[out])) out = s;
	}
	return out;
}

function asString(value: unknown): string | null {
	return typeof value === 'string' ? value : null;
}
function asNumber(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export class RaceHistoryReducer {
	private cursor = 0;
	private readonly lapsDone = new Map<string, number>();
	private readonly position = new Map<string, number>();
	private readonly gap = new Map<string, number | null>();
	private readonly series = new Map<string, Map<number, HistoryPoint>>();
	/** Drivers whose latest lap still waits for the gap sent as they
	 * crossed the line. */
	private readonly awaitingGap = new Set<string>();
	/** When each driver last crossed the line, and last had a gap sent. */
	private readonly crossedAt = new Map<string, string>();
	private readonly gapAt = new Map<string, string>();
	private readonly pits = new Map<string, Set<number>>();
	private readonly neutralByLap = new Map<number, Neutral>();
	/** Seen a `track_status_changed`: the feed's own status wins from then. */
	private feedStatus = false;
	/** Neutralisations open now (a VSC can be upgraded to an SC). */
	private open = new Set<Neutral>();
	/** Open when the leader began the lap being run. */
	private inherited = new Set<Neutral>();
	/** The worst one that opened during the lap being run. */
	private openedThisLap: Neutral | null = null;
	private leader = 0;
	/** Bumps whenever anything a view draws changed (a place, a lap's gap,
	 * a lap completed, a stop, a neutralisation), so views can skip
	 * recomputing on the many events that change none of it. */
	version = 0;

	update(events: readonly Envelope[]): void {
		if (events.length < this.cursor) this.reset();
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope !== undefined) this.apply(envelope);
		}
		this.cursor = events.length;
	}

	/** Laps the leader has completed. */
	get leaderLap(): number {
		return this.leader;
	}

	/** One point per lap the driver has been seen on, oldest first. */
	points(driver: string): HistoryPoint[] {
		const laps = this.series.get(driver);
		if (!laps) return [];
		return [...laps.values()].sort((a, b) => a.lap - b.lap);
	}

	/** Laps the driver came into the pits on (the in-lap's number). */
	pitLaps(driver: string): number[] {
		return [...(this.pits.get(driver) ?? [])].sort((a, b) => a - b);
	}

	/** Neutralised laps, merged into bands. A completed lap counts when a
	 * neutralisation opened during it or was still open at its end (a
	 * safety car that came in at the line leaves the restart lap green); the
	 * lap being run counts while one is open now. */
	bands(): NeutralBand[] {
		const out: NeutralBand[] = [];
		for (const lap of [...this.neutralByLap.keys(), this.leader + 1].sort((a, b) => a - b)) {
			const status = this.neutralOn(lap);
			if (status === null) continue;
			const last = out[out.length - 1];
			if (last && last.status === status && last.toLap === lap - 1) last.toLap = lap;
			else out.push({ fromLap: lap, toLap: lap, status });
		}
		return out;
	}

	/** The worst neutralisation on a lap, or null when it ran green. */
	neutralOn(lap: number): Neutral | null {
		if (lap === this.leader + 1)
			return worst([...this.open, ...(this.openedThisLap ? [this.openedThisLap] : [])]);
		return this.neutralByLap.get(lap) ?? null;
	}

	/** The gap from `behind` to `ahead` at the end of each lap both have a
	 * gap to the leader for, oldest first. */
	pairGaps(behind: string, ahead: string): { lap: number; gapS: number }[] {
		const a = this.series.get(behind);
		const b = this.series.get(ahead);
		if (!a || !b) return [];
		// The leader's own gap to the leader is never sent: it is zero.
		const gapOf = (p: HistoryPoint) => p.gapS ?? (p.position === 1 ? 0 : null);
		const out: { lap: number; gapS: number }[] = [];
		for (const [lap, p] of a) {
			const q = b.get(lap);
			const behindGap = gapOf(p);
			const aheadGap = q ? gapOf(q) : null;
			if (lap === 0 || behindGap === null || aheadGap === null) continue;
			out.push({ lap, gapS: Math.round((behindGap - aheadGap) * 1000) / 1000 });
		}
		return out.sort((x, y) => x.lap - y.lap);
	}

	reset(): void {
		this.cursor = 0;
		this.lapsDone.clear();
		this.position.clear();
		this.gap.clear();
		this.series.clear();
		this.awaitingGap.clear();
		this.crossedAt.clear();
		this.gapAt.clear();
		this.pits.clear();
		this.neutralByLap.clear();
		this.open = new Set();
		this.inherited = new Set();
		this.openedThisLap = null;
		this.feedStatus = false;
		this.leader = 0;
		this.version++;
	}

	private point(driver: string): HistoryPoint {
		const lap = this.lapsDone.get(driver) ?? 0;
		let laps = this.series.get(driver);
		if (!laps) {
			laps = new Map();
			this.series.set(driver, laps);
		}
		let p = laps.get(lap);
		if (!p) {
			p = {
				lap,
				position: this.position.get(driver) ?? null,
				gapS: this.gap.get(driver) ?? null
			};
			laps.set(lap, p);
		}
		return p;
	}

	/** Moves to a new set of open neutralisations, noting any that opened. */
	private setOpen(next: Set<Neutral>): void {
		if (next.size !== this.open.size || [...next].some((x) => !this.open.has(x))) this.version++;
		for (const status of next) {
			if (!this.open.has(status)) this.openedThisLap = worst([this.openedThisLap, status]);
		}
		this.open = next;
	}

	/** The leader finished `lap`: file how it ran, then start the next. */
	private finishLap(lap: number): void {
		// A VSC ends anywhere on the lap, so one carried in counts even if it
		// ended before the line; a safety car or red flag carried in and
		// lifted at the line leaves the lap green.
		const carriedVsc = this.inherited.has('vsc') ? 'vsc' : null;
		const status = worst([this.openedThisLap, carriedVsc, ...this.open]);
		if (status !== null) this.neutralByLap.set(lap, status);
		this.inherited = new Set(this.open);
		this.openedThisLap = null;
	}

	private apply(envelope: Envelope): void {
		const p = envelope.payload;
		switch (envelope.kind) {
			case 'position_changed': {
				const driver = asString(p.driver);
				const position = asNumber(p.position);
				if (driver === null || position === null) return;
				this.position.set(driver, position);
				const pt = this.point(driver);
				if (pt.position !== position) {
					pt.position = position;
					this.version++;
				}
				return;
			}
			case 'gap_updated': {
				const driver = asString(p.driver);
				if (driver === null) return;
				const lapped = asNumber(p.laps_down) !== null;
				const gap = lapped ? null : parseIsoDurationSeconds(p.gap_to_leader);
				this.gap.set(driver, gap);
				this.gapAt.set(driver, envelope.event_time_utc);
				// A lap's gap is the one sent as the car crossed the line: the
				// first after its lap completed, or one sent at the same instant.
				// Gaps sent later in the lap (at sector lines) leave it alone.
				if (
					this.awaitingGap.has(driver) ||
					!this.lapsDone.has(driver) ||
					this.crossedAt.get(driver) === envelope.event_time_utc
				) {
					const pt = this.point(driver);
					if (pt.gapS !== gap) {
						pt.gapS = gap;
						this.version++;
					}
					this.awaitingGap.delete(driver);
				}
				return;
			}
			case 'lap_completed': {
				const driver = asString(p.driver);
				const lap = asNumber(p.lap_number);
				if (driver === null || lap === null) return;
				if (lap <= (this.lapsDone.get(driver) ?? 0)) return;
				this.lapsDone.set(driver, lap);
				this.point(driver);
				this.version++;
				// The gap sent in the same instant as the crossing already
				// filled the point; otherwise wait for the next one.
				if (this.gapAt.get(driver) === envelope.event_time_utc) this.awaitingGap.delete(driver);
				else this.awaitingGap.add(driver);
				this.crossedAt.set(driver, envelope.event_time_utc);
				while (lap > this.leader) {
					this.leader += 1;
					this.finishLap(this.leader);
				}
				return;
			}
			case 'pit_entry': {
				const driver = asString(p.driver);
				if (driver === null) return;
				const laps = this.pits.get(driver) ?? new Set<number>();
				laps.add((this.lapsDone.get(driver) ?? 0) + 1);
				this.pits.set(driver, laps);
				this.version++;
				return;
			}
			case 'track_status_changed': {
				// The feed's own status replaces anything race control opened.
				this.feedStatus = true;
				const status = asString(p.status);
				this.setOpen(
					status === 'sc' || status === 'vsc' || status === 'red' ? new Set([status]) : new Set()
				);
				return;
			}
			case 'race_control_message': {
				if (this.feedStatus) return;
				this.setOpen(this.applyRaceControl(p));
				return;
			}
			default:
				return;
		}
	}

	/** The same reading `f1_kernel.status` gives race control: what is open
	 * after this message. */
	private applyRaceControl(p: Record<string, unknown>): Set<Neutral> {
		const open = new Set(this.open);
		const category = (asString(p.category) ?? '').trim().toLowerCase();
		const message = (asString(p.message) ?? '').trim().toUpperCase();
		const flag = (asString(p.flag) ?? '').trim().toUpperCase();
		const scope = (asString(p.scope) ?? '').trim().toLowerCase();
		if (category === 'safetycar') {
			const virtual = message.includes('VIRTUAL');
			if (message.includes('DEPLOYED')) {
				if (!virtual) open.delete('vsc');
				open.add(virtual ? 'vsc' : 'sc');
			} else if (message.includes('ENDING')) {
				open.delete(virtual ? 'vsc' : 'sc');
			}
			return open;
		}
		if (category !== 'flag' || scope === 'driver' || scope === 'sector') return open;
		if (flag === 'RED') {
			open.clear();
			open.add('red');
		} else if (flag === 'CLEAR' || flag === 'GREEN') {
			open.clear();
		}
		return open;
	}
}
