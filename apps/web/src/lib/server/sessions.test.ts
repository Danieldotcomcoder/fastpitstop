import { afterEach, describe, expect, it, vi } from 'vitest';

/** A fresh copy of the module each time: its cache is module state. */
async function load() {
	vi.resetModules();
	return import('./sessions');
}

function respond(rows: unknown[]): Response {
	return new Response(JSON.stringify(rows), { status: 200 });
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe('sessionRows', () => {
	it('waits for the first list, then serves an expired one at once while refreshing', async () => {
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(0);
		let release: (r: Response) => void = () => undefined;
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(respond([{ key: 'old' }]))
			.mockImplementationOnce(() => new Promise<Response>((resolve) => (release = resolve)));
		vi.stubGlobal('fetch', fetch);
		const { sessionRows } = await load();

		expect(await sessionRows()).toEqual([{ key: 'old' }]);
		vi.setSystemTime(60_000);
		// The API is slow: the expired list comes back without waiting.
		expect(await sessionRows()).toEqual([{ key: 'old' }]);
		expect(fetch).toHaveBeenCalledTimes(2);
		release(respond([{ key: 'new' }]));
		await vi.waitFor(async () => expect(await sessionRows()).toEqual([{ key: 'new' }]));
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('keeps the last good list when a refresh fails', async () => {
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(0);
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(respond([{ key: 'good' }]))
			.mockRejectedValue(new Error('down'));
		vi.stubGlobal('fetch', fetch);
		const { sessionRows } = await load();
		expect(await sessionRows()).toEqual([{ key: 'good' }]);
		vi.setSystemTime(60_000);
		expect(await sessionRows()).toEqual([{ key: 'good' }]);
		await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
		expect(await sessionRows()).toEqual([{ key: 'good' }]);
	});

	it('returns an empty list when the API never answered', async () => {
		vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
		const { sessionRows } = await load();
		expect(await sessionRows()).toEqual([]);
	});
});
