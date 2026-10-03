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
 * The offset is measured by round trip against `GET /time`: the server read
 * its clock about halfway between sending and receiving, and the sample with
 * the shortest round trip pins that halfway point most tightly. */

export interface ClockSample {
	/** Device clock just before the request, epoch ms. */
	sentMs: number;
	/** Device clock just after the response, epoch ms. */
	receivedMs: number;
	/** The server's clock in the response, epoch ms. */
	serverMs: number;
}

/** Below this an offset is network noise, not a wrong clock: correcting it
 * would only make releases jitter between page loads. */
export const NEGLIGIBLE_OFFSET_MS = 500;

/** What to add to the device clock to read the server's, from one round trip. */
export function offsetFromSample(sample: ClockSample): number {
	return sample.serverMs - (sample.sentMs + sample.receivedMs) / 2;
}

/** The offset from the shortest round trip, or 0 when there is no sample
 * or the device clock is already right to within `NEGLIGIBLE_OFFSET_MS`. */
export function bestOffset(samples: readonly ClockSample[]): number {
	if (samples.length === 0) return 0;
	const best = samples.reduce((a, b) =>
		b.receivedMs - b.sentMs < a.receivedMs - a.sentMs ? b : a
	);
	const offset = offsetFromSample(best);
	return Math.abs(offset) < NEGLIGIBLE_OFFSET_MS ? 0 : Math.round(offset);
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
