/** The viewer's clock, corrected to the server's.
 *
 * Every `event_time_utc` on the stream is the SERVER's clock: the live
 * ingest stamps events on receipt, and replay stamps each envelope as it is
 * sent. Delay Sync releases an envelope once the viewer's clock, minus their
 * delay, passes that instant, so it is only as right as the viewer's device
 * clock. A phone a minute fast released everything a minute early, which on
 * a broadcast delay is a spoiler; a minute slow showed "live" a minute late.
 * Every clock read that is compared with a stream timestamp goes through
 * `serverNowMs()` instead of `Date.now()`.
 *
 * The offset is measured by round trip against `GET /time`, and taken as
 * its LOWER BOUND: the server read its clock somewhere between the request
 * leaving and the response arriving, so "server time minus arrival time" is
 * never more than the true offset. Delay Sync on that bound can release a
 * moment late by up to a round trip, never early. It used to assume the
 * server read its clock halfway through, which a busy server breaks: while
 * the page's own heavy requests (the circuit geometry, the story) held the
 * API, `/time` was answered seconds late, the offset read seconds ahead, and
 * replays released up to two seconds before the viewer's delay
 * (`just verify-real`, 2026-10-11). */

export interface ClockSample {
	/** Device clock just before the request, epoch ms. */
	sentMs: number;
	/** Device clock just after the response, epoch ms. */
	receivedMs: number;
	/** The server's clock in the response, epoch ms. */
	serverMs: number;
}

/** A positive correction (the device clock slow) smaller than this is
 * network noise, not a wrong clock: correcting it would only make releases
 * jitter between page loads. A negative one is always applied: it can only
 * make a release later, never earlier. */
export const NEGLIGIBLE_OFFSET_MS = 500;

/** A lower bound on what to add to the device clock to read the server's,
 * from one round trip: the server's reading cannot postdate the response
 * arriving. */
export function offsetFromSample(sample: ClockSample): number {
	return sample.serverMs - sample.receivedMs;
}

/** The tightest lower bound across `samples` (the largest), or 0 when there
 * is no sample or it is a positive offset under `NEGLIGIBLE_OFFSET_MS`. */
export function bestOffset(samples: readonly ClockSample[]): number {
	if (samples.length === 0) return 0;
	const offset = Math.max(...samples.map(offsetFromSample));
	return offset >= 0 && offset < NEGLIGIBLE_OFFSET_MS ? 0 : Math.round(offset);
}

/** Measure the offset with `samples` round trips. A failed request is
 * skipped; if every one fails the answer is 0, which is exactly the
 * uncorrected behaviour, never worse. */
export async function measureServerOffset(
	readServerMs: () => Promise<number>,
	{ now = Date.now, samples = 3 }: { now?: () => number; samples?: number } = {}
): Promise<number> {
	const taken: ClockSample[] = [];
	for (let i = 0; i < samples; i++) {
		try {
			const sentMs = now();
			const serverMs = await readServerMs();
			const receivedMs = now();
			if (Number.isFinite(serverMs)) taken.push({ sentMs, receivedMs, serverMs });
		} catch {
			// One lost request says nothing about the clock; try the next.
		}
	}
	return bestOffset(taken);
}

let offsetMs = 0;

/** The current correction, ms to add to the device clock. */
export function serverClockOffsetMs(): number {
	return offsetMs;
}

export function setServerClockOffsetMs(value: number): void {
	offsetMs = Number.isFinite(value) ? value : 0;
}

/** Now, on the server's clock (epoch ms). Use wherever the result is
 * compared with an `event_time_utc`. */
export function serverNowMs(): number {
	return Date.now() + offsetMs;
}
