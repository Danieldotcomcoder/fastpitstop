/** Presentation switches: the underlying routes, components and data all
 * stay built either way — these only decide what a visitor is shown.
 *
 * Both default in code and can be overridden per-deployment by a
 * `PUBLIC_`-prefixed environment variable, read through
 * `$env/dynamic/public` so it is a runtime setting rather than something
 * baked into the build. Accepted values: `1`/`true`/`on` (case-insensitive)
 * to enable, `0`/`false`/`off` to disable.
 *
 * `SHOW_RACE_STORY` (default ON) gates the why-engine surfaces: the
 * `Race story` tab, the `Why` lane in the timing rail, and the `/story`
 * route. It is on because the story is the product's differentiator and it
 * degrades honestly — with no model key configured every explanation is
 * the canonical "No clear cause found in the available evidence." rather
 * than a guess or an error.
 *
 * `SHOW_RADIO` (default OFF) gates the team-radio surfaces. Off because
 * the corpus needs a season fetched and transcribed into Postgres, which
 * no default deployment has, so the lane and the search page would render
 * permanently empty rather than degraded. `just verify-phase4` sets
 * `PUBLIC_SHOW_RADIO=1` for its own fixture pair, which DOES have a
 * corpus — that override is why this is an env flag and not a constant.
 */
import { env } from '$env/dynamic/public';

const TRUE_VALUES = new Set(['1', 'true', 'on', 'yes']);
const FALSE_VALUES = new Set(['0', 'false', 'off', 'no']);

/** `fallback` unless the variable is set to something recognisable — an
 * unset or misspelled value must not silently flip a surface. */
export function flagFromEnv(raw: string | undefined, fallback: boolean): boolean {
	if (raw === undefined) return fallback;
	const normalized = raw.trim().toLowerCase();
	if (TRUE_VALUES.has(normalized)) return true;
	if (FALSE_VALUES.has(normalized)) return false;
	return fallback;
}

export const SHOW_RACE_STORY = flagFromEnv(env.PUBLIC_SHOW_RACE_STORY, true);
export const SHOW_RADIO = flagFromEnv(env.PUBLIC_SHOW_RADIO, false);
