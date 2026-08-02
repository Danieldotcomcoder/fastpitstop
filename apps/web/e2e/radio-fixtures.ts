/** Constants for Phase 4.5's E2E suite (`radio.spec.ts`) — mirrors
 * `python/f1_radio/fixtures/generate_e2e.py` exactly (the session key, the
 * four planted clips' driver order, and the one paraphrase query with a
 * planted embedding) so this suite exercises the SAME checked-in radio
 * fixture the backend's own `test_radio_fixture.py` does, not a second
 * invented one. */
import { RADIO_WEB_PORT } from './ports';

export const RADIO_WEB_BASE_URL = `http://127.0.0.1:${RADIO_WEB_PORT}`;

/** "Radio Fixture Grand Prix" (season 2024, round 1) — a dedicated
 * historical root carrying ONLY `team_radio.parquet` (see
 * `generate_e2e.py`'s own docstring for why this can't just be a third
 * session in the shared `f1_kernel` fixture). */
export const RADIO_SESSION_KEY = '2024-1-race';

/** Close to clip 0's own planted embedding, with NO lexical overlap against
 * its transcript ("Box box, box this lap for the hard tyre") — the
 * paraphrase search assertion. */
export const RADIO_PARAPHRASE_QUERY = "there's nothing left up front, box this lap";
export const RADIO_PARAPHRASE_EXPECTED_DRIVER = '44';
export const RADIO_PARAPHRASE_EXPECTED_TRANSCRIPT = 'Box box, box this lap for the hard tyre';

/** The four fixture clips' driver, in ascending `captured_at` order —
 * what the timeline lane should render once every clip has released. */
export const RADIO_EXPECTED_DRIVER_ORDER = ['44', '1', '44', '1'];

/** The one deliberately LOW-confidence clip (a safety-car call, confidence
 * 0.35 — below `f1_radio.transcription.DEFAULT_LOW_CONFIDENCE_THRESHOLD`),
 * so the frontend's low-confidence marking has something real to assert
 * against. */
export const RADIO_LOW_CONFIDENCE_TRANSCRIPT = 'Yellow yellow yellow, safety car deployed';

export const RADIO_E2E_SPEED = 0;
export const RADIO_E2E_DELAY_SECONDS = 8;

export function radioRaceUrl(opts: { speed?: number; delay?: number } = {}): string {
	const params = new URLSearchParams({
		key: RADIO_SESSION_KEY,
		speed: String(opts.speed ?? RADIO_E2E_SPEED),
		delay: String(opts.delay ?? RADIO_E2E_DELAY_SECONDS)
	});
	return `${RADIO_WEB_BASE_URL}/race?${params.toString()}`;
}

export function radioSearchPageUrl(): string {
	return `${RADIO_WEB_BASE_URL}/radio`;
}
