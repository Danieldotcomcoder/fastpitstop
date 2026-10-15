import type { PageServerLoad } from './$types';
import { circuitBrief, type CircuitBrief } from '$lib/live/circuit-brief';
import { circuitList, circuitProfile } from '$lib/server/circuits';
import { nextRace } from '$lib/server/schedule';
import { sessionRows } from '$lib/server/sessions';

/** What the page first shows, sent with it: the session list (to find a
 * running session) and the next race (the idle hub), plus what is measured
 * at its circuit. All are the web server's own short-lived copies, so a
 * phone paints the page without first asking the API (ROADMAP W7's
 * load-time target). The page still polls the API itself from then on. */
export const load: PageServerLoad = async () => {
	const [sessions, next] = await Promise.all([sessionRows(), nextRace()]);
	let circuit: CircuitBrief | null = null;
	const id = next?.event.circuit_id;
	if (id) {
		const [profile, all] = await Promise.all([circuitProfile(id).catch(() => null), circuitList()]);
		if (profile) circuit = circuitBrief(profile, all);
	}
	return { sessions, next, circuit };
};
