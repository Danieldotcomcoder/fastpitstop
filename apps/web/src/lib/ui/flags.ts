/** What the full-width flag banner says for each track status. Green says
 * nothing: the banner is for the moments the race is not running normally. */
import type { TrackStatusTone } from '$lib/desk/race-feed';

export interface Banner {
	tone: Exclude<TrackStatusTone, 'green'>;
	label: string;
}

const BANNERS: Record<Exclude<TrackStatusTone, 'green'>, string> = {
	yellow: 'Yellow flag',
	sc: 'Safety car',
	vsc: 'Virtual safety car',
	red: 'Red flag',
	chequered: 'Chequered flag'
};

export function bannerFor(tone: TrackStatusTone): Banner | null {
	return tone === 'green' ? null : { tone, label: BANNERS[tone] };
}
