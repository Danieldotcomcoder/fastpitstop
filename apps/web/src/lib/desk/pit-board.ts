/** The pit board: the undercut call for the focused battle, shaped for the
 * design's dark "Undercut watch" panel.
 *
 * Every number comes from the kernel's own `kernel_snapshot` crossover,
 * already released through Delay Sync: the window state, the margin once
 * both cars have stopped and its 95% band, the stop lap, the calibrated
 * pit loss. The one thing derived here is where the attacker would rejoin,
 * read off the released timing tower (gap to leader + that same pit loss),
 * exactly as the design does and labelled as traffic, not as a forecast. */
import type { components } from '$lib/api/client';
import type { DriverRow } from '$lib/timing-tower';
import type { FocusPair } from './focus';
import { compoundName } from './format';

export type Crossover = components['schemas']['CrossoverResultOut'];

export type BoardState = 'waiting' | 'pitting' | 'unknown' | 'open' | 'opens' | 'closed';

export interface BoardDriver {
	driver: string;
	code: string;
	position: number | null;
	team: string | null;
	compound: string | null;
	tyreAge: number | null;
	inPit: boolean;
}

/** Text with one emphasised span, so a view never needs `{@html}`. */
export interface Emphasised {
	before: string;
	em: string;
	after: string;
}

export interface Rejoin {
	position: number;
	behind: string | null;
	gapS: number | null;
	inTraffic: boolean;
}

export interface BoardModel {
	state: BoardState;
	attacker: BoardDriver | null;
	defender: BoardDriver | null;
	headline: Emphasised;
	sub: Emphasised;
	confidence: 'high' | 'medium' | 'low' | null;
	/** Once both cars have stopped: positive means the attacker is ahead. */
	marginS: number | null;
	lowS: number | null;
	highS: number | null;
	intervalS: number | null;
	boxAtLap: number | null;
	rejoin: Rejoin | null;
	pitLossS: number | null;
	pitLossNote: string;
}

/** The traffic threshold the design uses: rejoining within this of the
 * car ahead reads as "in traffic". */
export const TRAFFIC_S = 2.5;

function boardDriver(
	rows: readonly DriverRow[],
	driver: string,
	codes: Record<string, string>
): BoardDriver {
	const row = rows.find((r) => r.driver === driver);
	return {
		driver,
		code: codes[driver] ?? row?.code ?? driver,
		position: row?.position ?? null,
		team: row?.team ?? null,
		compound: row?.compound ?? null,
		tyreAge: row?.tyreAge ?? null,
		inPit: row?.inPit ?? false
	};
}

/** The undercut projection for exactly this attacker on this defender. */
export function crossoverFor(crossovers: readonly Crossover[], pair: FocusPair): Crossover | null {
	return (
		crossovers.find(
			(c) => c.kind === 'undercut' && c.attacker === pair.a && c.defender === pair.b
		) ?? null
	);
}

export function pitLossNote(provenance: string | null | undefined): string {
	switch (provenance) {
		case 'calibrated':
			return 'Calibrated, this circuit';
		case 'provisional':
			return 'Provisional, this circuit';
		case 'override':
			return 'Set by override';
		default:
			return 'Cross-circuit default';
	}
}

/** Where the attacker rejoins if they stop now: every car whose gap to the
 * leader is within the attacker's own gap plus the pit loss stays ahead. */
export function rejoinAfterStop(
	rows: readonly DriverRow[],
	attacker: string,
	pitLossS: number
): Rejoin | null {
	const self = rows.find((r) => r.driver === attacker);
	if (!self || self.gapToLeaderS === null) return null;
	const base = self.gapToLeaderS + pitLossS;
	let position = 1;
	let ahead: { driver: string; gap: number } | null = null;
	for (const row of rows) {
		if (row.driver === attacker) continue;
		const gap = row.position === 1 ? 0 : row.gapToLeaderS;
		if (gap === null || gap > base) continue;
		position += 1;
		if (ahead === null || gap > ahead.gap) ahead = { driver: row.driver, gap };
	}
	const gapS = ahead ? base - ahead.gap : null;
	return {
		position,
		behind: ahead?.driver ?? null,
		gapS,
		inTraffic: gapS !== null && gapS < TRAFFIC_S
	};
}

/** The car ahead on rejoin, named by its code as the board shows it. */
function withCode(rejoin: Rejoin | null, codes: Record<string, string>): Rejoin | null {
	if (rejoin === null || rejoin.behind === null) return rejoin;
	return { ...rejoin, behind: codes[rejoin.behind] ?? rejoin.behind };
}

function relation(marginS: number, defender: string): Emphasised {
	const behind = marginS < 0;
	return {
		before: '',
		em: `${Math.abs(marginS).toFixed(2)} s ${behind ? 'behind' : 'ahead of'} ${defender}`,
		after: ''
	};
}

const plain = (text: string): Emphasised => ({ before: text, em: '', after: '' });

export function buildBoard(
	rows: readonly DriverRow[],
	pair: FocusPair | null,
	crossover: Crossover | null,
	codes: Record<string, string>
): BoardModel {
	const empty: BoardModel = {
		state: 'waiting',
		attacker: null,
		defender: null,
		headline: plain('Waiting for the running order'),
		sub: plain('The undercut watch starts once two cars are on the timing screen.'),
		confidence: null,
		marginS: null,
		lowS: null,
		highS: null,
		intervalS: null,
		boxAtLap: null,
		rejoin: null,
		pitLossS: null,
		pitLossNote: pitLossNote(null)
	};
	if (pair === null) return empty;

	const attacker = boardDriver(rows, pair.a, codes);
	const defender = boardDriver(rows, pair.b, codes);
	const interval = rows.find((r) => r.driver === pair.a)?.intervalToAheadS ?? null;
	const base: BoardModel = {
		...empty,
		attacker,
		defender,
		intervalS: attacker.inPit ? null : interval,
		pitLossS: crossover?.pit_loss_s ?? null,
		pitLossNote: pitLossNote(crossover?.pit_loss_provenance)
	};

	if (attacker.inPit || defender.inPit) {
		const who = attacker.inPit ? attacker : defender;
		return {
			...base,
			state: 'pitting',
			headline: { before: '', em: who.code, after: ' is in the pit lane' },
			sub: plain(
				attacker.inPit
					? `The undercut is on. The result shows here once ${defender.code} has stopped too.`
					: `${defender.code} is stopping first. ${attacker.code} can now try the overcut.`
			)
		};
	}

	const margin = crossover?.projected_gap_if_covered_s ?? null;
	if (crossover === null || crossover.window === 'unknown' || margin === null) {
		const reason = crossover?.reasons?.[0];
		return {
			...base,
			state: 'unknown',
			headline: plain('Not enough clean laps yet'),
			sub: plain(
				`${reason ? `${reason.replace(/\.$/, '')}. ` : ''}The estimate returns once both fits have enough clean laps.`
			)
		};
	}

	const result = relation(margin, defender.code);
	const withNumbers: BoardModel = {
		...base,
		confidence: crossover.confidence,
		marginS: margin,
		lowS: crossover.projected_gap_if_covered_low_s ?? null,
		highS: crossover.projected_gap_if_covered_high_s ?? null,
		rejoin: withCode(rejoinAfterStop(rows, pair.a, crossover.pit_loss_s), codes)
	};

	if (crossover.window === 'open') {
		return {
			...withNumbers,
			state: 'open',
			boxAtLap: crossover.attacker_stop_lap ?? crossover.decision_lap + 1,
			headline: { before: 'Window ', em: 'open', after: ', box now' },
			sub: {
				before: `If ${attacker.code} pits at the end of this lap and ${defender.code} covers a lap later, ${attacker.code} comes out `,
				em: result.em,
				after: '.'
			}
		};
	}
	if (crossover.window === 'opens_later') {
		const inLaps = crossover.opens_in_laps ?? 1;
		return {
			...withNumbers,
			state: 'opens',
			boxAtLap: crossover.decision_lap + 1 + inLaps,
			headline: {
				before: 'Window opens in ',
				em: `${inLaps} lap${inLaps === 1 ? '' : 's'}`,
				after: ''
			},
			sub: {
				before: `Pit now and ${attacker.code} comes out `,
				em: result.em,
				after: ' once both have stopped.'
			}
		};
	}
	return {
		...withNumbers,
		state: 'closed',
		boxAtLap: null,
		headline: { before: 'Window ', em: 'closed', after: '' },
		sub: {
			before: `Pit now and ${attacker.code} comes out `,
			em: result.em,
			after: ' once both have stopped, and the gap isn’t closing.'
		}
	};
}

/** "NOR medium 12 laps · VER hard 18 laps" under the pair. */
export function tyresLine(model: BoardModel): string {
	const part = (d: BoardDriver | null) =>
		d
			? `${d.code} ${compoundName(d.compound).toLowerCase()}${d.tyreAge !== null ? ` ${d.tyreAge} laps` : ''}`
			: '';
	return [part(model.attacker), part(model.defender)].filter(Boolean).join(' · ');
}
