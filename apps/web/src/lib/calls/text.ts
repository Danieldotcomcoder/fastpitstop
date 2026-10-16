/** What a call card says (ROADMAP W5): what was called, and what the race
 * did with it. The words come only from the call's own fields (the kernel's
 * `f1_kernel.Call`), so a card never claims more than the ledger holds.
 *
 * Laps, never clock times: a replay re-times its stream, and a lap reads
 * the same in both. */
import type { CallOut, CallStatus } from './view';

export interface CallText {
	/** "L13". The lap the car boxed at the end of. */
	lap: string;
	kind: 'Undercut' | 'Rejoin';
	/** What was called, and (once settled) what happened. */
	body: string;
	/** What a waiting call waits for, or why it is not counted. */
	note: string | null;
	status: CallStatus;
	/** The word beside the mark: the colour is never the only signal. */
	outcome: 'Right' | 'Wrong' | 'Waiting' | 'Not counted' | 'No call';
}

const OUTCOME: Record<CallStatus, CallText['outcome']> = {
	right: 'Right',
	wrong: 'Wrong',
	pending: 'Waiting',
	void: 'Not counted',
	no_call: 'No call'
};

function undercutNote(call: CallOut, rival: string, driver: string): string | null {
	switch (call.detail) {
		case 'waiting_for_rival_stop':
			return `Settles when ${rival} stops.`;
		case 'waiting_for_rival_out_lap':
		case 'waiting_for_outcome_lap':
			return `Settles when ${rival} finishes the out-lap.`;
		case 'rival_never_stopped_again':
			return `${rival} never stopped again, so the two never both changed tyres.`;
		case 'double_stacked_or_simultaneous':
			return `${rival} stopped on the same lap as ${driver}.`;
		case 'rival_stopped_first':
			return `${rival} had stopped the lap before: it was never an undercut on ${rival}.`;
		case 'neutralized_between_stops':
			return 'A safety car or virtual safety car came out between the stops.';
		case 'rival_stop_not_green':
			return `${rival}'s stop was not a green-flag stop.`;
		case 'no_common_outcome_lap':
			return 'The two never completed the same lap once both had stopped.';
		default:
			return call.status === 'no_call' ? 'Not enough clean laps to call it.' : null;
	}
}

function rejoinNote(call: CallOut, driver: string, ahead: string | null): string | null {
	switch (call.detail) {
		case 'waiting_for_out_lap':
			return 'Settles at the end of the out-lap.';
		case 'waiting_for_cars_around':
			return 'Settles once the cars around the rejoin have finished that lap.';
		case 'another_car_pitted_around_the_rejoin':
			return `A car around where ${driver} rejoined pitted on the same laps, so the order we projected was never on offer.`;
		case 'retired_in_the_pits':
			return `${driver} retired in the pits.`;
		case 'stop_not_green':
			return 'The stop was not made under green flags.';
		case 'projected_car_ahead_did_not_finish_the_lap':
			return `${ahead ?? 'The car we named'} did not finish that lap.`;
		default:
			return null;
	}
}

function undercutBody(call: CallOut, driver: string, rival: string): string {
	const opening = `${driver} boxed to undercut ${rival}.`;
	if (call.status === 'no_call' || call.window === 'unknown') return opening;
	if (call.status === 'void') return opening;
	const claim =
		call.crossover_lap !== null
			? `We said it comes through if ${rival} stays out to lap ${call.crossover_lap}.`
			: 'We said it would not come through.';
	if ((call.status === 'right' || call.status === 'wrong') && call.rival_stop_lap !== null) {
		const after =
			call.driver_ahead_after === null
				? ''
				: ` ${driver} came out ${call.driver_ahead_after ? 'ahead' : 'behind'}.`;
		return `${opening} ${claim} ${rival} stopped on lap ${call.rival_stop_lap}.${after}`;
	}
	return `${opening} ${claim}`;
}

function rejoinBody(call: CallOut, driver: string, name: (driver: string) => string): string {
	const said =
		call.projected_position === null
			? null
			: call.rival !== null
				? `P${call.projected_position}, behind ${name(call.rival)}`
				: `P${call.projected_position}, in the lead`;
	const air =
		call.projected_margin_s !== null && call.rival !== null
			? `, ${call.projected_margin_s.toFixed(1)} s from the nearest car`
			: '';
	if (call.status === 'void') return said ? `${driver}: ${said}.` : `${driver} boxed.`;
	const opening = said ? `${driver} boxed. We said ${said}${air}.` : `${driver} boxed.`;
	if ((call.status === 'right' || call.status === 'wrong') && call.actual_position !== null) {
		const behind =
			call.actual_car_ahead !== null ? `, behind ${name(call.actual_car_ahead)}` : ', in the lead';
		return `${opening} ${driver} came out P${call.actual_position}${behind}.`;
	}
	return opening;
}

/** One call as its card reads. `name` gives the name a sentence uses. */
export function callText(call: CallOut, name: (driver: string) => string): CallText {
	const driver = name(call.driver);
	const rival = call.rival !== null ? name(call.rival) : null;
	const isUndercut = call.kind === 'undercut';
	return {
		lap: `L${call.in_lap}`,
		kind: isUndercut ? 'Undercut' : 'Rejoin',
		body: isUndercut
			? undercutBody(call, driver, rival ?? 'the car ahead')
			: rejoinBody(call, driver, name),
		note: isUndercut
			? undercutNote(call, rival ?? 'the car ahead', driver)
			: rejoinNote(call, driver, rival),
		status: call.status,
		outcome: OUTCOME[call.status]
	};
}

/** The ledger's three groups (the mockup's "counted, waiting and not
 * counted"); a no-call joins the last, never scored either way. */
export function groupOf(call: CallOut): 'counted' | 'waiting' | 'not_counted' {
	if (call.status === 'right' || call.status === 'wrong') return 'counted';
	if (call.status === 'pending') return 'waiting';
	return 'not_counted';
}

/** Newest first: the lap the car boxed, then the order calls locked. */
export function latestFirst(calls: readonly CallOut[]): CallOut[] {
	return calls
		.map((call, index) => ({ call, index }))
		.sort((a, b) => b.call.in_lap - a.call.in_lap || b.index - a.index)
		.map(({ call }) => call);
}
