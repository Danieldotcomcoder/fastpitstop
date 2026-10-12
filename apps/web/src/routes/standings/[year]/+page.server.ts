import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

/** One season's standings: the same page as `/standings`, for an earlier
 * season. */
export const ssr = true;

export const load: PageServerLoad = ({ params }) => {
	const current = new Date().getUTCFullYear();
	const season = Number(params.year);
	if (!/^\d{4}$/.test(params.year) || season < 2018 || season > current) {
		error(404, 'No such season');
	}
	return { season, seasons: [current, current - 1, current - 2] };
};
