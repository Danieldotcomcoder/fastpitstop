/** The standings page (ROADMAP M2): which rounds the viewer has seen, the
 * sentence under the chart and the chart itself. Pure, so the spoiler rule
 * and the geometry are tested rather than eyeballed. */
import type { components } from '$lib/api/schema';

export type StandingsOut = components['schemas']['StandingsOut'];
export type StandingsRound = components['schemas']['StandingsRoundOut'];
export type DriverStanding = components['schemas']['DriverStandingOut'];
export type RemainingPoints = components['schemas']['RemainingPointsOut'];

/** A race this recent may not have been watched yet: the page leaves it
 * out until the viewer says they have seen it. */
export const RECENT_DAYS = 7;

/** The round the page counts up to when the viewer hasn't chosen: every
 * round except those whose Grand Prix ran in the last `RECENT_DAYS`. A
 * round with no known date counts as recent if it is the latest: unknown
 * is never assumed safe. 0 when nothing is old enough. */
export function defaultCutoff(rounds: readonly StandingsRound[], nowMs: number): number {
	const sorted = [...rounds].sort((a, b) => a.round - b.round);
	const recentFrom = nowMs - RECENT_DAYS * 86_400_000;
	let cutoff = 0;
	for (const [i, r] of sorted.entries()) {
		const started = r.race_start_utc ? Date.parse(r.race_start_utc) : null;
		const old = started === null ? i < sorted.length - 1 : started <= recentFrom;
		// In round order: everything after the first recent round is newer.
		if (!old) break;
		cutoff = r.round;
	}
	return cutoff;
}

const KEY = (season: number) => `fps.standings.watched.${season}`;

/** The round the viewer said they've watched up to this season, if any. */
export function readWatched(
	season: number,
	storage: Pick<Storage, 'getItem'> | null
): number | null {
	try {
		const raw = storage?.getItem(KEY(season));
		if (raw === null || raw === undefined) return null;
		const value = Number(raw);
		return Number.isInteger(value) && value >= 0 ? value : null;
	} catch {
		return null;
	}
}

export function storeWatched(
	season: number,
	round: number,
	storage: Pick<Storage, 'setItem'> | null
): void {
	try {
		storage?.setItem(KEY(season), String(round));
	} catch {
		// Storage blocked: the choice lasts for this visit.
	}
}

/** The rounds the page doesn't count, for the shield's line. */
export function hiddenRounds(rounds: readonly StandingsRound[], cutoff: number): StandingsRound[] {
	return rounds.filter((r) => r.round > cutoff);
}

/** "Verstappen leads Pérez by 33." and what is still available. */
export function leaderSentence(
	drivers: readonly DriverStanding[],
	remaining: RemainingPoints | null | undefined
): string {
	const [first, second] = drivers;
	if (!first) return '';
	const parts: string[] = [];
	if (second) {
		const lead = first.points - second.points;
		parts.push(
			lead === 0
				? `${first.name} and ${second.name} are level on ${points(first.points)}.`
				: `${first.name} leads ${second.name} by ${points(lead)}.`
		);
	}
	if (remaining && remaining.max_points > 0) {
		const races = `${remaining.races} ${remaining.races === 1 ? 'race' : 'races'} at ${remaining.race_points}`;
		const sprints =
			remaining.sprints > 0
				? ` and ${remaining.sprints} ${remaining.sprints === 1 ? 'sprint' : 'sprints'} at ${remaining.sprint_points}`
				: '';
		parts.push(`Up to ${remaining.max_points} points are still available: ${races}${sprints}.`);
		if (second && first.points - second.points > remaining.max_points) {
			parts.push(`${first.name} can no longer be caught.`);
		}
	} else if (remaining && remaining.max_points === 0 && second) {
		// Nothing after the counted rounds: the season is over.
		parts.length = 0;
		parts.push(`${first.name} took the title by ${points(first.points - second.points)}.`);
	}
	return parts.join(' ');
}

const points = (value: number) => `${Number.isInteger(value) ? value : value.toFixed(1)}`;

export interface ChartLine {
	code: string;
	color: string;
	dashed: boolean;
	points: string;
	label: { x: number; y: number; text: string };
}

export interface ProgressionChart {
	lines: ChartLine[];
	grid: { y: number; value: number }[];
	rounds: { x: number; label: string }[];
}

/** Running points after each round for the leading drivers, as SVG
 * polylines. A team's second driver is dashed, and the end labels are
 * nudged apart so close totals stay readable. */
export function progressionChart(
	drivers: readonly DriverStanding[],
	roundLabels: readonly string[],
	width: number,
	height: number,
	top = 6
): ProgressionChart | null {
	const shown = drivers.slice(0, top);
	const count = shown[0]?.by_round.length ?? 0;
	if (count === 0) return null;
	const pad = { left: 34, right: 70, top: 10, bottom: 24 };
	const high = Math.max(1, ...shown.map((d) => d.points));
	const x = (i: number) =>
		count === 1
			? pad.left + (width - pad.left - pad.right) / 2
			: pad.left + (i * (width - pad.left - pad.right)) / (count - 1);
	const y = (v: number) => pad.top + (1 - v / high) * (height - pad.top - pad.bottom);
	const r1 = (v: number) => Math.round(v * 10) / 10;

	const step = niceStep(high);
	const grid = [];
	for (let v = 0; v <= high; v += step) grid.push({ y: r1(y(v)), value: v });

	const labelY = new Map<string, number>();
	let last = -Infinity;
	for (const d of [...shown].sort((a, b) => y(a.points) - y(b.points))) {
		const at = Math.max(y(d.points) + 4, last + 12);
		labelY.set(d.code, r1(at));
		last = at;
	}

	const seen = new Set<string>();
	const lines = shown.map((d) => {
		const team = d.team ?? d.code;
		const dashed = seen.has(team);
		seen.add(team);
		return {
			code: d.code,
			color: d.team_color ?? 'var(--ink-2)',
			dashed,
			points: d.by_round.map((v, i) => `${r1(x(i))},${r1(y(v))}`).join(' '),
			label: {
				x: r1(x(count - 1) + 6),
				y: labelY.get(d.code)!,
				text: `${d.code} ${points(d.points)}`
			}
		};
	});

	const every = Math.max(1, Math.ceil(count / 12));
	const rounds = roundLabels
		.slice(0, count)
		.map((label, i) => ({ x: r1(x(i)), label }))
		// Every `every`-th round and the last, never one crowding the last.
		.filter((_, i) => i === count - 1 || (i % every === 0 && count - 1 - i >= every));
	return { lines, grid, rounds };
}

/** 50 for a 136-point leader, 100 for 437: four to eight grid lines. */
function niceStep(high: number): number {
	for (const step of [5, 10, 25, 50, 100, 200]) if (high / step <= 6) return step;
	return 250;
}
