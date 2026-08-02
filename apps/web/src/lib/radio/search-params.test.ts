import { describe, expect, it } from 'vitest';
import {
	buildRadioSearchQuery,
	EMPTY_RADIO_SEARCH_FILTERS,
	hasAnyRadioFilter,
	type RadioSearchFilters
} from './search-params';

function filters(overrides: Partial<RadioSearchFilters>): RadioSearchFilters {
	return { ...EMPTY_RADIO_SEARCH_FILTERS, ...overrides };
}

describe('buildRadioSearchQuery', () => {
	it('always includes q, even blank', () => {
		expect(buildRadioSearchQuery(EMPTY_RADIO_SEARCH_FILTERS)).toEqual({ q: '' });
	});

	it('trims the query text', () => {
		expect(buildRadioSearchQuery(filters({ query: '  box box  ' }))).toEqual({ q: 'box box' });
	});

	it('omits blank optional fields entirely rather than sending them empty', () => {
		const query = buildRadioSearchQuery(filters({ query: 'box', driver: '  ' }));
		expect(query).toEqual({ q: 'box' });
		expect('driver' in query).toBe(false);
	});

	it('includes driver/session/track_status when set', () => {
		const query = buildRadioSearchQuery(
			filters({ query: 'box', driver: '44', session: '2024-1-race', trackStatus: 'sc' })
		);
		expect(query).toEqual({ q: 'box', driver: '44', session: '2024-1-race', track_status: 'sc' });
	});

	it('converts from/to date inputs to ISO instants', () => {
		const query = buildRadioSearchQuery(
			filters({ query: '', from: '2024-05-01', to: '2024-05-02' })
		);
		expect(query.from).toBe(new Date('2024-05-01').toISOString());
		expect(query.to).toBe(new Date('2024-05-02').toISOString());
	});
});

describe('hasAnyRadioFilter', () => {
	it('is false when everything is blank', () => {
		expect(hasAnyRadioFilter(EMPTY_RADIO_SEARCH_FILTERS)).toBe(false);
	});
	it('is true when only a non-query filter is set', () => {
		expect(hasAnyRadioFilter(filters({ driver: '44' }))).toBe(true);
	});
	it('is true when only the query text is set', () => {
		expect(hasAnyRadioFilter(filters({ query: 'box box' }))).toBe(true);
	});
});
