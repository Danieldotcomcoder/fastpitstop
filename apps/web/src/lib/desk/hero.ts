/** The desktop desk's new views per mockup A (ROADMAP W2): the battle card
 * that leads the page, the race ribbon ("How the top ten got here") and the
 * one-line read of the field beside the map. Pure functions over the timing
 * tower's rows and the released race history (`race-history.ts`), so none
 * of them can know more than the viewer's clock has released. */
import type { DriverRow } from '$lib/timing-tower';
import type { BoardDriver } from './pit-board';
import type { Neutral, NeutralBand, RaceHistoryReducer } from './race-history';

// --- the battle -------------------------------------------------------------

/** How far back the battle card's trend reads. */
export const BATTLE_LAPS = 8;

/** Gaps within this of each other read as holding steady. */
const STEADY_S = 0.1;

export interface BattlePoint {
	lap: number;
	gapS: number;
	/** The lap ran under a safety car, VSC or red flag. */
	neutral: Neutral | null;
}

export interface BattleTrend {
	points: BattlePoint[];
	fromLap: number;
	fromS: number;
	nowS: number;
	direction: 'down' | 'up' | 'steady';
	/** How much of the change came on neutralised laps (same units, signed). */
	neutralChangeS: number;
	/** The worst neutralisation inside the window, for the sentence. */
	neutral: Neutral | null;
}

const NEUTRAL_RANK: Record<Neutral, number> = { vsc: 1, sc: 2, red: 3 };

/** The focused pair's gap over the last `BATTLE_LAPS` laps, end of lap by
 * end of lap, from the released history. Null under two measured laps. */
export function battleTrend(
	history: RaceHistoryReducer,
	behind: string,
	ahead: string,
	span = BATTLE_LAPS
): BattleTrend | null {
	const gaps = history.pairGaps(behind, ahead);
	const last = gaps[gaps.length - 1];
	if (!last) return null;
	const window = gaps.filter((g) => g.lap >= last.lap - span);
	const first = window[0];
	if (!first || first === last) return null;
	const points = window.map((g) => ({ ...g, neutral: history.neutralOn(g.lap) }));
	let neutralChangeS = 0;
	let neutral: Neutral | null = null;
	for (let i = 1; i < points.length; i++) {
		const p = points[i] as BattlePoint;
		const q = points[i - 1] as BattlePoint;
		if (p.neutral === null) continue;
		neutralChangeS += p.gapS - q.gapS;
		if (neutral === null || NEUTRAL_RANK[p.neutral] > NEUTRAL_RANK[neutral]) neutral = p.neutral;
	}
	const change = last.gapS - first.gapS;
	return {
		points,
		fromLap: first.lap,
		fromS: first.gapS,
		nowS: last.gapS,
		direction: Math.abs(change) < STEADY_S ? 'steady' : change < 0 ? 'down' : 'up',
		neutralChangeS,
		neutral
	};
}

const NEUTRAL_NAME: Record<Neutral, string> = {
	sc: 'the safety car',
	vsc: 'the virtual safety car',
	red: 'the red flag'
};

/** The sentence under the battle: how the gap moved, how much of that was
 * a neutralisation, then what the tyres say. Every clause is a fact the
 * card's own numbers show; nothing is predicted here. */
export function battleVerdict(
	trend: BattleTrend | null,
	behind: BoardDriver | null,
	ahead: BoardDriver | null,
	behindName: string,
	aheadName: string
): string {
	const parts: string[] = [];
	let mostlyNeutral = false;
	if (trend) {
		const total = trend.nowS - trend.fromS;
		if (trend.direction === 'steady') {
			parts.push(`Holding at about ${trend.nowS.toFixed(1)} s since lap ${trend.fromLap}.`);
		} else {
			let move = `${trend.direction === 'down' ? 'Down' : 'Up'} from ${trend.fromS.toFixed(1)} s on lap ${trend.fromLap}`;
			if (trend.neutral !== null) {
				mostlyNeutral =
					Math.sign(trend.neutralChangeS) === Math.sign(total) &&
					Math.abs(trend.neutralChangeS) >= Math.abs(total) / 2;
				move += mostlyNeutral
					? `, most of it behind ${NEUTRAL_NAME[trend.neutral]} (shaded)`
					: `; the laps behind ${NEUTRAL_NAME[trend.neutral]} are shaded`;
			}
			parts.push(`${move}.`);
		}
	}
	if (behind && ahead && behind.compound && ahead.compound) {
		const sameCompound = behind.compound === ahead.compound;
		const ageGap =
			behind.tyreAge !== null && ahead.tyreAge !== null ? behind.tyreAge - ahead.tyreAge : null;
		if (sameCompound && ageGap !== null && Math.abs(ageGap) <= 1) {
			parts.push(
				trend?.direction === 'down' && !mostlyNeutral
					? 'Same tyres, same age: this one is on pace alone.'
					: 'Same tyres, same age.'
			);
		} else if (ageGap !== null && Math.abs(ageGap) > 1) {
			const fresher = ageGap < 0 ? behindName : aheadName;
			const tyre = sameCompound
				? 'tyres'
				: `${(ageGap < 0 ? behind : ahead).compound?.toLowerCase()}s`;
			parts.push(`${fresher}'s ${tyre} are ${Math.abs(ageGap)} laps fresher.`);
		} else if (!sameCompound) {
			parts.push(
				`${behindName} on ${behind.compound.toLowerCase()}s, ${aheadName} on ${ahead.compound.toLowerCase()}s.`
			);
		}
	}
	return parts.join(' ');
}

// --- the race ribbon ----------------------------------------------------------

export const RIBBON_TOP = 10;

export interface RibbonLine {
	driver: string;
	code: string;
	team: string | null;
	position: number;
	/** Second car of its team: drawn dashed, so teammates differ by more
	 * than colour (W2 D1). */
	dashed: boolean;
	points: { lap: number; position: number }[];
	pitLaps: number[];
}

export interface RibbonModel {
	lines: RibbonLine[];
	bands: NeutralBand[];
	/** Laps the leader has completed: the ribbon ends here. */
	reached: number;
	/** The x-axis end: the race distance when the feed announced it, else
	 * the lap reached (a backfilled replay never says, and its finishing
	 * lap count would give a shortened race away). */
	axisEnd: number;
	/** Bottom of the y-axis: the field size. */
	fieldSize: number;
}

/** Dashed for the second car of a team, by car number so it never flips
 * when teammates swap places. */
export function teammateDashed(rows: readonly DriverRow[]): Record<string, boolean> {
	const byNumber = [...rows].sort(
		(a, b) => (Number(a.driver) || 0) - (Number(b.driver) || 0) || a.driver.localeCompare(b.driver)
	);
	const seen = new Set<string>();
	const out: Record<string, boolean> = {};
	for (const row of byNumber) {
		const team = row.team?.toLowerCase().trim() ?? null;
		out[row.driver] = team !== null && seen.has(team);
		if (team !== null) seen.add(team);
	}
	return out;
}

export function ribbonModel(
	history: RaceHistoryReducer,
	rows: readonly DriverRow[],
	codes: Record<string, string>,
	totalLaps: number | null,
	top = RIBBON_TOP
): RibbonModel {
	const dashed = teammateDashed(rows);
	const running = rows.filter(
		(r): r is DriverRow & { position: number } => r.position !== null && !r.retired
	);
	const lines: RibbonLine[] = running.slice(0, top).map((row) => ({
		driver: row.driver,
		code: codes[row.driver] ?? row.code ?? row.driver,
		team: row.team,
		position: row.position,
		dashed: dashed[row.driver] ?? false,
		points: history
			.points(row.driver)
			.filter((p): p is typeof p & { position: number } => p.position !== null)
			.map((p) => ({ lap: p.lap, position: p.position })),
		pitLaps: history.pitLaps(row.driver)
	}));
	const reached = history.leaderLap;
	return {
		lines,
		bands: history.bands().filter((b) => b.fromLap <= Math.max(reached, 1)),
		reached,
		axisEnd: Math.max(totalLaps ?? reached, reached, 1),
		fieldSize: Math.max(rows.length, 2)
	};
}

// --- the field, in one line ---------------------------------------------------

/** One line beside the map: who leads and by how much, and how spread out
 * the lead lap is. Facts from the released tower only. */
export function fieldLine(
	rows: readonly DriverRow[],
	codes: Record<string, string>
): string | null {
	const running = rows.filter((r) => r.position !== null && !r.retired);
	const leader = running.find((r) => r.position === 1);
	if (!leader) return null;
	const code = (r: DriverRow) => codes[r.driver] ?? r.code ?? r.driver;
	const parts: string[] = [];
	const second = running.find((r) => r.position === 2);
	const margin = second?.intervalToAheadS ?? second?.gapToLeaderS ?? null;
	parts.push(
		margin !== null && second?.lapsDown === null
			? `${code(leader)} leads by ${margin.toFixed(1)} s.`
			: `${code(leader)} leads.`
	);
	const leadLap = running.filter(
		(r) => r.position === 1 || (r.lapsDown === null && r.gapToLeaderS !== null)
	);
	const lapped = running.filter((r) => r.lapsDown !== null && r.position !== 1).length;
	if (leadLap.length >= 3) {
		const spread = Math.max(...leadLap.map((r) => r.gapToLeaderS ?? 0));
		parts.push(`${leadLap.length} cars on the lead lap, covered by ${spread.toFixed(1)} s.`);
	}
	if (lapped > 0) parts.push(`${lapped} lapped.`);
	return parts.join(' ');
}
