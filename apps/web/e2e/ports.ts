/** Ports for every `webServer` pair `playwright.config.ts` boots — the one
 * shared source of truth so a spec file building an absolute URL (needed
 * whenever it must target a SPECIFIC pair rather than the config's default
 * `baseURL`) never repeats a magic number the config itself might drift
 * from. */

/** Phase 3.6's pair: `f1_api` over the `f1_kernel` checked-in fixture, and
 * the frontend pointed at it — the config's own default `baseURL`. */
export const API_PORT = 8834;
export const WEB_PORT = 4319;

/** Phase 4.5's pair: `f1_api` over the dedicated radio E2E fixture
 * (`python/f1_radio/tests/fixtures/e2e_historical` +
 * `e2e_radio_fixture.json`, `F1_API_RADIO_FIXTURE`-driven, see
 * `f1_api.radio_fixture`), and its OWN frontend instance — a second pair
 * rather than reusing the Phase 3 one because a running SvelteKit process
 * reads `PUBLIC_F1_API_BASE_URL` once at boot, so pointing at two different
 * `f1_api` backends needs two frontend processes, not a runtime switch. */
export const RADIO_API_PORT = 8837;
export const RADIO_WEB_PORT = 4320;

/** The release gate's pair (`just verify-real`, `playwright.real.config.ts`):
 * `f1_api` over the REAL local backfill (`data/historical/`) and a frontend
 * pointed at it. */
export const REAL_API_PORT = 8840;
export const REAL_WEB_PORT = 4321;
