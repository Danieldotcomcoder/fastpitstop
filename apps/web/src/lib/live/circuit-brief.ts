/** What `/live` shows about the next round's circuit before its weekend
 * (ROADMAP W2, the "Before the weekend" panel): the same measured figures
 * as the circuit page, never this weekend's own. */
import { circuitSlug } from '$lib/circuit/map';
import {
	pitLossView,
	wearRows,
	wearSeasons,
	type PitLossInput,
	type PitLossView,
	type WearInput,
	type WearRow
} from '$lib/circuit/profile';

export interface CircuitBrief {
	slug: string;
	name: string;
	lapKm: number;
	corners: number;
	drsZones: number;
	pitLoss: PitLossView;
	/** The quickest and slowest measured stops anywhere, for the scale. */
	range: { low: number; high: number } | null;
	wear: WearRow[];
	wearSeasons: string;
}

export interface CircuitBriefInput extends PitLossInput {
	name: string;
	lap_distance_m: number;
	corners: readonly unknown[];
	drs_zones: readonly unknown[];
	tyre_wear: readonly WearInput[];
}

export function circuitBrief(
	profile: CircuitBriefInput,
	all: readonly PitLossInput[]
): CircuitBrief {
	const measured = all.filter((c) => c.pit_loss_provenance === 'calibrated');
	return {
		slug: circuitSlug(profile.circuit_id),
		name: profile.name,
		lapKm: profile.lap_distance_m / 1000,
		corners: profile.corners.length,
		drsZones: profile.drs_zones.length,
		pitLoss: pitLossView(profile, all.length > 0 ? all : [profile]),
		range:
			measured.length > 1
				? {
						low: Math.min(...measured.map((c) => c.pit_loss_s)),
						high: Math.max(...measured.map((c) => c.pit_loss_s))
					}
				: null,
		wear: wearRows(profile.tyre_wear),
		wearSeasons: wearSeasons(profile.tyre_wear)
	};
}
