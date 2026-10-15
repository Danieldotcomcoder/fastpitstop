/** The story page held back to the viewer's lap (ROADMAP W2, mockup
 * "Story"): only moments the viewer has reached, grouped by what kind of
 * moment they are, with a count of what is still held back. The race's
 * own length is never shown: a short race would give itself away. */
import { storyMomentRevealed, type StoryMoment } from './story';

export type Phase = 'start' | 'track' | 'strategy' | 'incidents' | 'finish';

export const PHASES: { id: Phase; title: string }[] = [
	{ id: 'start', title: 'The start' },
	{ id: 'track', title: 'On track' },
	{ id: 'strategy', title: 'Strategy' },
	{ id: 'incidents', title: 'Incidents' },
	{ id: 'finish', title: 'The finish' }
];

const PHASE_OF: Record<string, Phase> = {
	race_start: 'start',
	position_change: 'track',
	undercut_resolved: 'strategy',
	pit_anomaly: 'strategy',
	deg_cliff: 'strategy',
	neutralization: 'incidents',
	retirement: 'incidents',
	pace_loss: 'incidents',
	off_track: 'incidents',
	race_finish: 'finish'
};

export function phaseOf(kind: string): Phase {
	return PHASE_OF[kind] ?? 'incidents';
}

export interface HeldStory {
	/** Moments at or before the viewer's lap, in race order. */
	shown: StoryMoment[];
	/** How many are still held back. */
	heldBack: number;
	groups: { id: Phase; title: string; moments: StoryMoment[] }[];
}

/** `upTo` is the last lap the viewer has watched; null means the whole
 * race. A moment shows once its reveal lap is reached, as on the desk. */
export function holdStory(moments: readonly StoryMoment[], upTo: number | null): HeldStory {
	const shown = moments.filter((m) =>
		upTo === null ? true : storyMomentRevealed(m, (_driver, lap) => lap <= upTo, false)
	);
	return {
		shown,
		heldBack: moments.length - shown.length,
		groups: PHASES.map((p) => ({
			...p,
			moments: shown.filter((m) => phaseOf(m.kind) === p.id)
		})).filter((g) => g.moments.length > 0)
	};
}

/** The lap a moment sits at on the axis. */
export function momentLap(m: StoryMoment): number | null {
	return m.revealLap ?? m.lapNumber;
}

const KEY = 'story-upto:';

/** The lap the viewer said they had reached for this session, remembered
 * on the device; null when they chose the whole race, undefined when they
 * have not said. */
export function storedUpTo(sessionKey: string): number | null | undefined {
	try {
		const raw = localStorage.getItem(KEY + sessionKey);
		if (raw === null) return undefined;
		if (raw === 'all') return null;
		const lap = Number(raw);
		return Number.isInteger(lap) && lap >= 0 ? lap : undefined;
	} catch {
		return undefined;
	}
}

export function storeUpTo(sessionKey: string, upTo: number | null): void {
	try {
		localStorage.setItem(KEY + sessionKey, upTo === null ? 'all' : String(upTo));
	} catch {
		// Storage blocked: the choice lasts as long as the page.
	}
}
