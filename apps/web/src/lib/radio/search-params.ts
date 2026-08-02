/** Pure translation from the corpus search page's own filter state to
 * `GET /radio/search`'s query params (`f1_api.routes.radio.search_radio`) —
 * kept separate from the Svelte component so the "which fields are blank,
 * which get trimmed, which get dropped entirely" logic has a real unit
 * test with no DOM, mirroring `$lib/kernel-views/gap-stream.ts`'s own
 * "framework-agnostic core, thin Svelte glue" split. */

export interface RadioSearchFilters {
	query: string;
	driver: string;
	session: string;
	from: string;
	to: string;
	trackStatus: string;
}

export const EMPTY_RADIO_SEARCH_FILTERS: RadioSearchFilters = Object.freeze({
	query: '',
	driver: '',
	session: '',
	from: '',
	to: '',
	trackStatus: ''
});

export interface RadioSearchQuery {
	q: string;
	driver?: string;
	session?: string;
	from?: string;
	to?: string;
	track_status?: string;
}

/** `q` is always present (blank runs a pure structured/keyword search on
 * the backend, per that route's own docstring); every other field is
 * trimmed and OMITTED entirely when blank rather than sent as an empty
 * string, so `openapi-fetch` never serializes `driver=` into the URL. */
export function buildRadioSearchQuery(filters: RadioSearchFilters): RadioSearchQuery {
	const query: RadioSearchQuery = { q: filters.query.trim() };
	const driver = filters.driver.trim();
	if (driver) query.driver = driver;
	const session = filters.session.trim();
	if (session) query.session = session;
	const from = filters.from.trim();
	if (from) query.from = new Date(from).toISOString();
	const to = filters.to.trim();
	if (to) query.to = new Date(to).toISOString();
	const trackStatus = filters.trackStatus.trim();
	if (trackStatus) query.track_status = trackStatus;
	return query;
}

/** Whether any filter (including the query text) is actually set — used to
 * decide between "empty state: type to search" and "no results for this
 * search" without re-deriving the same blank-checks the component already
 * has scattered around it. */
export function hasAnyRadioFilter(filters: RadioSearchFilters): boolean {
	return (
		filters.query.trim() !== '' ||
		filters.driver.trim() !== '' ||
		filters.session.trim() !== '' ||
		filters.from.trim() !== '' ||
		filters.to.trim() !== '' ||
		filters.trackStatus.trim() !== ''
	);
}
