/** Shared shapes for the track map (Phase 3.4). Defined here rather than
 * imported from `$lib/api/schema` (the generated REST client) so this
 * module's pure geometry/animation core stays decoupled from the API
 * layer's own types — the same reasoning `$lib/delay-sync/envelope.ts`
 * gives for redefining `Envelope` instead of importing `StreamEnvelope`.
 * `TrackOutline` is structurally identical to `f1_api.schemas.
 * TrackOutlineOut` / the generated `components['schemas']['TrackOutlineOut']`,
 * so a real API response satisfies it with no conversion. */

/** A point in the circuit's own RAW coordinate units — the same units
 * `car_position` envelopes carry (`f1_domain.CarPosition`'s feed units,
 * ~1/10 m), on purpose: `f1_kernel.track_outline.derive_track_outline`
 * deliberately does not center or rescale the outline, so a live position
 * sample plots directly alongside it with no extra transform. Fitting this
 * to a canvas (aspect-preserving scale-to-canvas) is `geometry.ts`'s job,
 * done once per render and reused for the outline and every car marker so
 * the two can never drift apart. */
export type Point = readonly [number, number];

export interface TrackOutline {
	points: Point[];
	width_m: number;
	height_m: number;
	source_points: number;
}

export interface PositionSample {
	x: number;
	y: number;
	tsMs: number;
}

/** One car's known state, folded from `car_position` / `driver_list_updated`
 * / `lap_completed` / `stint_started` envelopes. `current`/`previous` are
 * the two most recent RAW position samples (never interpolated values) —
 * `$lib/track-map/interpolation` is what turns them into a smooth position
 * for a given render timestamp. `current` stays `null` for a driver the
 * feed has named (`driver_list_updated`) but not yet placed on track —
 * nothing to render yet, not an error. */
export interface CarMarker {
	driver: string;
	code: string | null;
	team: string | null;
	compound: string | null;
	current: PositionSample | null;
	previous: PositionSample | null;
}
