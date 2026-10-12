/** Qualifying, as released so far (ROADMAP W4): folds the delay-synced
 * event log into the current part, its clock, each driver's best lap in
 * every part and who is out, then answers what the qualifying board shows:
 * the order in this part, the cut, what each car in the drop zone needs,
 * and the knockouts so far.
 *
 * Reads only envelopes Delay Sync has released, like every other desk
 * reducer, so it cannot get ahead of the viewer's clock. The clock between
 * updates is extrapolated from the viewer's own playback time. */
import type { Envelope } from '$lib/delay-sync';
import { parseIsoDurationSeconds } from '$lib/kernel-views';

export interface QualiDriver {
	driver: string;
	code: string | null;
	team: string | null;
	/** Best lap per part (index 0 = Q1), seconds. */
	best: (number | null)[];
	/** When each part's best was set, event time in ms. */
	bestAt: (number | null)[];
	/** The part this driver was knocked out in, if they were. */
	knockedOutIn: number | null;
	/** Unknown until the log says (a viewer joining mid-session). */
	inPit: boolean | null;
	/** Laps completed since leaving the pits; 0 is the out lap. */
	lapsSinceExit: number | null;
}

export interface QualiClock {
	remainingS: number;
	running: boolean;
	/** Event time of the reading, ms. */
	atMs: number;
}

export interface QualiState {
	part: number | null;
	entries: number[] | null;
	clock: QualiClock | null;
	drivers: Map<string, QualiDriver>;
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function driverOf(state: QualiState, number: string): QualiDriver {
	let d = state.drivers.get(number);
	if (!d) {
		d = {
			driver: number,
			code: null,
			team: null,
			best: [null, null, null],
			bestAt: [null, null, null],
			knockedOutIn: null,
			inPit: null,
			lapsSinceExit: null
		};
		state.drivers.set(number, d);
	}
	return d;
}

export function applyQualiEnvelope(state: QualiState, envelope: Envelope): void {
	const p = envelope.payload;
	const at = Date.parse(envelope.event_time_utc);
	switch (envelope.kind) {
		case 'qualifying_part_changed': {
			const part = num(p.part);
			if (part !== null) state.part = part;
			if (Array.isArray(p.entries)) {
				const entries = p.entries.map(num).filter((n): n is number => n !== null);
				if (entries.length) state.entries = entries;
			}
			return;
		}
		case 'qualifying_best_lap': {
			const driver = str(p.driver);
			const part = num(p.part);
			if (driver === null || part === null || part < 1 || part > 3) return;
			const d = driverOf(state, driver);
			d.best[part - 1] = parseIsoDurationSeconds(p.lap_time);
			d.bestAt[part - 1] = at;
			return;
		}
		case 'qualifying_knockout': {
			const driver = str(p.driver);
			if (driver === null) return;
			const d = driverOf(state, driver);
			d.knockedOutIn = p.knocked_out === false ? null : num(p.part);
			return;
		}
		case 'session_clock_updated': {
			const remaining = parseIsoDurationSeconds(p.remaining);
			if (remaining === null) return;
			state.clock = { remainingS: remaining, running: p.running === true, atMs: at };
			return;
		}
		case 'pit_entry': {
			const driver = str(p.driver);
			if (driver !== null) driverOf(state, driver).inPit = true;
			return;
		}
		case 'pit_exit': {
			const driver = str(p.driver);
			if (driver === null) return;
			const d = driverOf(state, driver);
			d.inPit = false;
			d.lapsSinceExit = 0;
			return;
		}
		case 'lap_completed': {
			const driver = str(p.driver);
			if (driver === null) return;
			const d = driverOf(state, driver);
			if (d.lapsSinceExit !== null) d.lapsSinceExit += 1;
			return;
		}
		case 'driver_list_updated': {
			if (!Array.isArray(p.drivers)) return;
			for (const ref of p.drivers) {
				if (typeof ref !== 'object' || ref === null) continue;
				const r = ref as Record<string, unknown>;
				const number = str(r.number);
				if (number === null) continue;
				const d = driverOf(state, number);
				d.code = str(r.code) ?? d.code;
				d.team = str(r.team) ?? d.team;
			}
			return;
		}
	}
}

export class QualifyingReducer {
	readonly state: QualiState = { part: null, entries: null, clock: null, drivers: new Map() };
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope) applyQualiEnvelope(this.state, envelope);
		}
		this.cursor = events.length;
	}

	reset(): void {
		this.state.part = null;
		this.state.entries = null;
		this.state.clock = null;
		this.state.drivers.clear();
		this.cursor = 0;
	}
}

// --- what the board shows ----------------------------------------------------

export type CarStatus = 'pit' | 'out' | 'track' | null;

export interface QualiRow {
	driver: string;
	code: string;
	team: string | null;
	/** Null until the car sets a time in this part: it has no place yet. */
	position: number | null;
	best: number | null;
	/** Behind the quickest in this part, seconds. */
	gap: number | null;
	status: CarStatus;
	/** Below the cut: out unless they improve. */
	inDanger: boolean;
	/** Improved within the last 30 seconds of session time. */
	improved: boolean;
}

export interface Knockouts {
	part: number;
	rows: {
		driver: string;
		code: string;
		team: string | null;
		position: number;
		best: number | null;
	}[];
}

export type ClockState = 'waiting' | 'running' | 'stopped' | 'over';

export interface QualiView {
	part: number | null;
	/** "Q2", or "SQ2" in sprint qualifying. */
	label: string | null;
	clockS: number | null;
	clockState: ClockState;
	/** How many go through from this part; null in the last part. */
	advance: number | null;
	rows: QualiRow[];
	/** The time on the last car through, if it has one. */
	cutTime: number | null;
	knockouts: Knockouts[];
}

const RECENT_S = 30;

function statusOf(d: QualiDriver): CarStatus {
	if (d.inPit === null) return null;
	if (d.inPit) return 'pit';
	return d.lapsSinceExit === 0 ? 'out' : 'track';
}

/** The board as of the viewer's playback time `nowMs`. `timeScale` is the
 * replay speed: event times are wall times, so a 16x replay's clock runs
 * 16 session seconds per wall second. */
export function qualiView(
	state: QualiState,
	nowMs: number,
	{ sprint = false, timeScale = 1 }: { sprint?: boolean; timeScale?: number } = {}
): QualiView {
	const part = state.part && state.part >= 1 ? state.part : null;
	const index = (part ?? 1) - 1;
	const elapsed = (at: number) => (Math.max(0, nowMs - at) / 1000) * timeScale;

	let clockS: number | null = null;
	let clockState: ClockState = 'waiting';
	if (part !== null && state.clock) {
		const c = state.clock;
		clockS = c.running ? Math.max(0, c.remainingS - elapsed(c.atMs)) : c.remainingS;
		clockState = c.running ? (clockS > 0 ? 'running' : 'over') : clockS > 0 ? 'stopped' : 'over';
	}

	const all = [...state.drivers.values()];
	const still = all.filter((d) => d.knockedOutIn === null);
	const order = (a: QualiDriver, b: QualiDriver, i: number) => {
		const x = a.best[i] ?? null;
		const y = b.best[i] ?? null;
		if (x !== null && y !== null && x !== y) return x - y;
		if (x !== null && y === null) return -1;
		if (x === null && y !== null) return 1;
		// Equal times: the one set first ranks ahead, as in F1.
		const ax = a.bestAt[i] ?? Infinity;
		const bx = b.bestAt[i] ?? Infinity;
		if (ax !== bx) return ax - bx;
		return Number(a.driver) - Number(b.driver);
	};
	// Cars with a time in this part, quickest first; then those without
	// one, in the order of the part before (no place of their own yet).
	const timed = still.filter((d) => d.best[index] != null).sort((a, b) => order(a, b, index));
	const untimed = still
		.filter((d) => d.best[index] == null)
		.sort((a, b) => (index > 0 ? order(a, b, index - 1) : Number(a.driver) - Number(b.driver)));
	const advance = part !== null && part < 3 ? (state.entries?.[part] ?? null) : null;
	// An untimed car is in danger once every place through is taken.
	const full = advance !== null && timed.length >= advance;
	const leader = timed[0]?.best[index] ?? null;
	const rows: QualiRow[] = [...timed, ...untimed].map((d, i) => {
		const best = d.best[index] ?? null;
		const at = d.bestAt[index];
		const position = best !== null ? i + 1 : null;
		return {
			driver: d.driver,
			code: d.code ?? d.driver,
			team: d.team,
			position,
			best,
			gap: best !== null && leader !== null ? best - leader : null,
			status: statusOf(d),
			inDanger: advance !== null && (position !== null ? position > advance : full),
			improved: best !== null && at !== null && at !== undefined && elapsed(at) < RECENT_S
		};
	});
	const cutTime =
		advance !== null && full && rows.length > advance ? (rows[advance - 1]?.best ?? null) : null;

	const knockouts: Knockouts[] = [];
	for (const p of [1, 2]) {
		const out = all.filter((d) => d.knockedOutIn === p).sort((a, b) => order(a, b, p - 1));
		if (!out.length) continue;
		const through = state.entries?.[p] ?? all.length - out.length;
		knockouts.push({
			part: p,
			rows: out.map((d, i) => ({
				driver: d.driver,
				code: d.code ?? d.driver,
				team: d.team,
				position: through + i + 1,
				best: d.best[p - 1] ?? null
			}))
		});
	}

	return {
		part,
		label: part ? `${sprint ? 'SQ' : 'Q'}${part}` : null,
		clockS,
		clockState,
		advance,
		rows,
		cutTime,
		knockouts: knockouts.reverse()
	};
}

/** The lap a car in the drop zone must beat: a thousandth under the cut,
 * since an equal time ranks behind the one set first. */
export function needsToBeat(cutTime: number): number {
	return Math.round((cutTime - 0.001) * 1000) / 1000;
}

/** "Ocon, Hamilton and Albon need 1:28.323 or better." and the margin of
 * the last car through. `name` turns a row into a display name. */
export function dropZoneSentences(
	view: QualiView,
	name: (row: QualiRow) => string,
	lap: (seconds: number) => string
): string[] {
	if (view.advance === null) return [];
	const danger = view.rows.filter((r) => r.inDanger);
	if (!danger.length) return [];
	const label = `${view.label?.startsWith('SQ') ? 'SQ' : 'Q'}${(view.part ?? 0) + 1}`;
	const names = danger.map(name);
	const list =
		names.length === 1 ? names[0]! : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
	const sentences: string[] = [];
	if (view.cutTime === null) {
		sentences.push(
			`${list} ${danger.length === 1 ? 'needs' : 'need'} any timed lap to reach ${label}.`
		);
	} else {
		sentences.push(
			`${list} ${danger.length === 1 ? 'needs' : 'need'} ${lap(needsToBeat(view.cutTime))} or better to reach ${label}.`
		);
		const lastIn = view.rows[view.advance - 1];
		const firstOut = view.rows[view.advance];
		if (lastIn?.best != null && firstOut?.best != null) {
			const margin = (firstOut.best - lastIn.best).toFixed(3);
			sentences.push(
				`${name(lastIn)} holds the last place in ${label} by ${margin} s over ${name(firstOut)}.`
			);
		}
	}
	return sentences;
}
