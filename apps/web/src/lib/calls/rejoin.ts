/** The rejoin call as the desk draws it (ROADMAP W5, "if he boxes now"):
 * where the focused car comes out if it boxes at the end of this lap, read
 * straight off the kernel's projection in the per-lap snapshot
 * (`f1_kernel.rejoin`). Nothing is recomputed here: the position, the cars
 * either side, the gaps, the pit loss and how much one stop varies are the
 * kernel's, and the confidence and its reason too.
 *
 * The snapshot carries projections only when rejoin calls are served
 * (`f1_api.stream.replay.kernel_snapshot_envelope`), so a kind that has not
 * passed its held-out backtest never reaches this module's output. */
import type { components } from '$lib/api/schema';
import type { KernelSnapshotPayload } from '$lib/delay-sync';
import type { CallConfidence, CallKind } from './view';

export type RejoinProjection = components['schemas']['RejoinProjectionOut'];

const CONFIDENCES: readonly CallConfidence[] = ['high', 'medium', 'low'];
const KINDS: readonly CallKind[] = ['undercut', 'rejoin'];

function isProjection(value: unknown): value is RejoinProjection {
	if (typeof value !== 'object' || value === null) return false;
	const v = value as Record<string, unknown>;
	return (
		typeof v.driver === 'string' &&
		typeof v.position === 'number' &&
		typeof v.position_now === 'number' &&
		typeof v.decision_lap === 'number' &&
		typeof v.pit_loss_s === 'number' &&
		typeof v.confidence === 'string' &&
		CONFIDENCES.includes(v.confidence as CallConfidence)
	);
}

/** The released snapshot's projections, checked. */
export function rejoinsFromSnapshot(
	snapshot: Pick<KernelSnapshotPayload, 'rejoins'> | null | undefined
): RejoinProjection[] {
	const raw = snapshot?.rejoins;
	return Array.isArray(raw) ? raw.filter(isProjection) : [];
}

/** The call kinds the stream serves, as the latest snapshot says. */
export function callKindsFromSnapshot(
	snapshot: Pick<KernelSnapshotPayload, 'call_kinds'> | null | undefined
): CallKind[] {
	const raw = snapshot?.call_kinds;
	return Array.isArray(raw)
		? raw.filter((k): k is CallKind => typeof k === 'string' && KINDS.includes(k as CallKind))
		: [];
}

export interface StripCar {
	driver: string;
	label: string;
	team: string | null;
	/** Seconds behind the focused car's crossing of the decision lap. */
	atS: number;
}

export interface StripModel {
	/** "If Leclerc boxes at the end of this lap". */
	kicker: string;
	/** "P7, 3.6 s behind Russell". */
	headline: string;
	/** "1.1 s ahead of Norris. Down three places from P4." */
	sub: string;
	confidence: CallConfidence;
	/** Why the confidence is what it is: the kernel's reasons after its
	 * first, as sentences. None when nothing is near the rejoin. */
	reason: string | null;
	/** The kernel's standing caveat (its first reason), as a sentence. */
	caveat: string | null;
	pitLossS: number;
	sigmaS: number | null;
	/** Where it comes out: `pitLossS` on the axis. */
	out: StripCar;
	ahead: StripCar | null;
	behind: StripCar | null;
	/** Cars further up the road than `ahead`, not drawn. */
	moreAhead: number;
	domain: { lo: number; hi: number };
	ticks: number[];
	/** The image's text alternative. */
	label: string;
}

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'];
const places = (n: number) =>
	`${n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n)} place${n === 1 ? '' : 's'}`;

function sentence(text: string): string {
	const trimmed = text.trim().replace(/\.$/, '');
	return trimmed.length === 0 ? '' : `${trimmed.charAt(0).toUpperCase()}${trimmed.slice(1)}.`;
}

/** The focused car's projection, shaped for the strip. `name` gives the
 * name a sentence uses (a surname), `code` the strip's three letters. */
export function stripModel(
	projection: RejoinProjection,
	name: (driver: string) => string,
	code: (driver: string) => string,
	team: (driver: string) => string | null
): StripModel {
	const p = projection;
	const loss = p.pit_loss_s;
	const who = name(p.driver);
	const headline =
		p.car_ahead !== null && p.gap_to_ahead_s !== null
			? `P${p.position}, ${p.gap_to_ahead_s.toFixed(1)} s behind ${name(p.car_ahead)}`
			: `P${p.position}, in the lead`;
	const parts: string[] = [];
	if (p.car_behind !== null && p.gap_to_behind_s !== null) {
		parts.push(`${p.gap_to_behind_s.toFixed(1)} s ahead of ${name(p.car_behind)}.`);
	}
	const lost = p.position - p.position_now;
	parts.push(
		lost > 0
			? `Down ${places(lost)} from P${p.position_now}.`
			: lost < 0
				? `Up ${places(-lost)} from P${p.position_now}.`
				: `Keeps P${p.position_now}.`
	);

	const out: StripCar = {
		driver: p.driver,
		label: code(p.driver),
		team: team(p.driver),
		atS: loss
	};
	const ahead =
		p.car_ahead !== null && p.gap_to_ahead_s !== null
			? {
					driver: p.car_ahead,
					label: code(p.car_ahead),
					team: team(p.car_ahead),
					atS: loss - p.gap_to_ahead_s
				}
			: null;
	const behind =
		p.car_behind !== null && p.gap_to_behind_s !== null
			? {
					driver: p.car_behind,
					label: code(p.car_behind),
					team: team(p.car_behind),
					atS: loss + p.gap_to_behind_s
				}
			: null;

	const band = 2 * (p.pit_loss_sigma_s ?? 0);
	const xs = [loss - band, loss + band, ...[ahead, behind].flatMap((c) => (c ? [c.atS] : []))];
	const lo = Math.floor((Math.min(...xs) - 1.5) / 5) * 5;
	const hi = Math.ceil((Math.max(...xs) + 1.5) / 5) * 5;
	const ticks: number[] = [];
	for (let v = lo; v <= hi; v += 5) ticks.push(v);

	// `f1_kernel.rejoin` always opens with what it does not model; the
	// reasons after it say why the confidence is what it is.
	const caveat = p.reasons[0] ? sentence(p.reasons[0]) : null;
	const why = p.reasons.slice(1).map(sentence).filter(Boolean);
	const reason = why.length > 0 ? why.join(' ') : null;
	const label =
		`If ${who} boxes now the stop costs ${loss.toFixed(1)} s: ` +
		(ahead && p.gap_to_ahead_s !== null
			? `out ${p.gap_to_ahead_s.toFixed(1)} s behind ${name(ahead.driver)}`
			: 'out in the lead') +
		(behind && p.gap_to_behind_s !== null
			? `, ${p.gap_to_behind_s.toFixed(1)} s ahead of ${name(behind.driver)}.`
			: '.') +
		(p.pit_loss_sigma_s !== null
			? ` The shaded bands are one and two times the ${p.pit_loss_sigma_s.toFixed(1)} s one stop varies by here.`
			: '');

	return {
		kicker: `If ${who} boxes at the end of this lap`,
		headline,
		sub: parts.join(' '),
		confidence: p.confidence,
		reason,
		caveat,
		pitLossS: loss,
		sigmaS: p.pit_loss_sigma_s,
		out,
		ahead,
		behind,
		moreAhead: Math.max(0, p.position - 1 - (ahead ? 1 : 0)),
		domain: { lo, hi: hi > lo ? hi : lo + 5 },
		ticks,
		label
	};
}
