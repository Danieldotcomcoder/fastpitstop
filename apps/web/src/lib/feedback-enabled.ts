import { env } from '$env/dynamic/public';

/** Whether the beta feedback form is served by this deployment.
 *
 * Mirrors the API's own `F1_FEEDBACK_ENABLED`: when that is off the
 * feedback router is not mounted at all, so linking to the form would
 * walk the user into a POST that 404s. Defaults to ON so dev and CI are
 * unaffected -- only an explicit '0'/'false' turns it off. */
export function feedbackEnabled(): boolean {
	const raw = env.PUBLIC_F1_FEEDBACK_ENABLED;
	if (raw === undefined || raw === null || raw === '') return true;
	return !['0', 'false', 'False'].includes(raw.trim());
}
