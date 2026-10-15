/** The project's published numbers, quoted on the about page (ROADMAP W2,
 * mockup "About"): each copied from a report committed with the code and
 * checked against it by `published.test.ts`, so a re-run report that moves
 * a number fails the build until the page says the same. */
import { MODEL_RECORD } from './model-record';

export const PUBLISHED = {
	undercut: MODEL_RECORD,
	/** `python/f1_kernel/reports/pit_loss.md`. */
	pitLoss: {
		season: 2024,
		stops: 410,
		races: 23,
		lowest: { circuit: 'Spa', seconds: 18.8 },
		highest: { circuit: 'Singapore', seconds: 30.1 }
	},
	/** `python/f1_kernel/reports/fuel_coefficient.md`. */
	fuel: { sPerKg: 0.0456, stints: 780, races: 23 },
	/** `python/f1_why/reports/story_eval.md`. */
	story: { races: 10, cards: 110, borneOut: 110, headlines: 35, found: 35 }
} as const;
