/** What a circuit page says about its numbers (ROADMAP S2). Pure, so the
 * wording and the honesty thresholds are tested rather than eyeballed. */

export type Tier = 'good' | 'thin' | 'unreliable';

/** A compound's wear rests on this many reliable stint fits: 8 or more
 * is a body of evidence, 3 to 7 is thin, under 3 is one or two stints. */
export const WEAR_GOOD_FITS = 8;
export const WEAR_THIN_FITS = 3;

export interface PitLossInput {
	circuit_id: string;
	pit_loss_s: number;
	pit_loss_provenance: string;
	pit_loss_uncertainty_s: number | null;
	pit_loss_n_stops: number | null;
}

export interface PitLossView {
	value: string;
	measured: boolean;
	/** "± 0.5 s · measured over 18 stops", or why it isn't measured. */
	detail: string;
	/** "highest of 19 measured circuits", or null when it isn't measured. */
	rank: string | null;
	tier: Tier;
}

const ORDINAL = ['', '', '2nd', '3rd', '4th', '5th'];

export function pitLossView(circuit: PitLossInput, all: readonly PitLossInput[]): PitLossView {
	const value = `${circuit.pit_loss_s.toFixed(1)} s`;
	const measured = circuit.pit_loss_provenance === 'calibrated';
	if (!measured) {
		return {
			value,
			measured,
			detail: 'Not measured here yet: the median of every measured circuit',
			rank: null,
			tier: 'unreliable'
		};
	}
	const unc = circuit.pit_loss_uncertainty_s;
	const stops = circuit.pit_loss_n_stops;
	const parts = [
		unc === null ? null : `± ${unc.toFixed(1)} s`,
		stops === null ? null : `measured over ${stops} stop${stops === 1 ? '' : 's'}`
	].filter((p): p is string => p !== null);
	const others = all.filter((c) => c.pit_loss_provenance === 'calibrated');
	const sorted = [...others].sort((a, b) => b.pit_loss_s - a.pit_loss_s);
	const place = sorted.findIndex((c) => c.circuit_id === circuit.circuit_id);
	let rank: string | null = null;
	if (place >= 0 && sorted.length > 2) {
		const fromTop = place + 1;
		const fromBottom = sorted.length - place;
		const of = `of ${sorted.length} measured circuits`;
		if (fromTop === 1) rank = `highest ${of}`;
		else if (fromBottom === 1) rank = `lowest ${of}`;
		else if (fromTop <= 5) rank = `${ORDINAL[fromTop]} highest ${of}`;
		else if (fromBottom <= 5) rank = `${ORDINAL[fromBottom]} lowest ${of}`;
	}
	return {
		value,
		measured,
		detail: parts.join(' · '),
		rank,
		tier: (stops ?? 0) >= 15 && (unc ?? Infinity) <= 0.6 ? 'good' : 'thin'
	};
}

export interface WearInput {
	compound: string;
	median_s_per_lap: number;
	good_fits: number;
	sessions: readonly string[];
}

export interface WearRow {
	compound: string;
	sPerLap: number;
	label: string;
	/** Bar length, 0..1 of the widest bar on the page. */
	fraction: number;
	fits: number;
	tier: Tier;
}

const DRY_ORDER = ['soft', 'medium', 'hard'];

/** Softest first, each with the evidence behind it. A compound whose
 * median wear is negative (tyres getting faster with age, a track
 * rubbering in) is drawn as no bar, not a backwards one. */
export function wearRows(wear: readonly WearInput[]): WearRow[] {
	const rows = DRY_ORDER.flatMap((compound) => wear.filter((w) => w.compound === compound));
	const widest = Math.max(0.05, ...rows.map((w) => w.median_s_per_lap));
	return rows.map((w) => {
		const tier: Tier =
			w.good_fits >= WEAR_GOOD_FITS
				? 'good'
				: w.good_fits >= WEAR_THIN_FITS
					? 'thin'
					: 'unreliable';
		const sign = w.median_s_per_lap < 0 ? '−' : '+';
		return {
			compound: w.compound,
			sPerLap: w.median_s_per_lap,
			label: `${sign}${Math.abs(w.median_s_per_lap).toFixed(3)} s/lap`,
			fraction: Math.max(0, w.median_s_per_lap) / widest,
			fits: w.good_fits,
			tier
		};
	});
}

/** The races the wear figures came from, as years ("2024 · 2025"). */
export function wearSeasons(wear: readonly WearInput[]): string {
	const years = new Set(wear.flatMap((w) => w.sessions.map((s) => s.slice(0, 4))));
	return [...years].sort().join(' · ');
}
