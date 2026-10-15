/** The share image of a Grand Prix page (ROADMAP S6): what a link to
 * `/races/{year}/{slug}` previews as on X, Facebook, WhatsApp or iMessage.
 * The circuit drawn from its real outline, the Grand Prix's name, round,
 * venue and dates, and the unofficial mark. Pure (SVG text in, nothing
 * fetched), so it is tested without a renderer; `$lib/server/og` turns it
 * into the PNG those apps need.
 *
 * Built from the calendar and the circuit file only, so it can hold no
 * result: nothing from a session ever reaches it. */
import type { Point } from '$lib/track-map/types';
import { outlinePath } from '$lib/ui/geometry';

export const CARD = { width: 1200, height: 630 } as const;

/** The dark theme's own colours (`tokens.css`), so a card looks like the
 * site it links to. */
const COLOR = {
	paper: '#030311',
	panel: '#08081b',
	rule: '#15152e',
	ink: '#eef0fa',
	ink2: '#bcc0d8',
	muted: '#8f93b3',
	accent: '#93a6ff',
	track: '#16163a'
} as const;

export interface ShareCard {
	season: number;
	round: number;
	eventName: string;
	/** The circuit, or the town when no circuit file knows the venue. */
	venue: string;
	/** "23–25 Oct", at the track. */
	dates: string;
	outline: readonly Point[] | null;
}

function escapeXml(value: string): string {
	return value
		.replaceAll('&', '&amp;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;')
		.replaceAll('"', '&quot;');
}

/** The name split over at most two lines, so it never runs into the
 * circuit: "United States" / "Grand Prix". */
export function nameLines(eventName: string, maxChars = 16): string[] {
	const words = eventName.split(/\s+/).filter(Boolean);
	if (eventName.length <= maxChars || words.length < 2) return [eventName];
	// The split that keeps the longer line shortest.
	let best: [string, string] = [eventName, ''];
	let bestLen = Infinity;
	for (let i = 1; i < words.length; i++) {
		const a = words.slice(0, i).join(' ');
		const b = words.slice(i).join(' ');
		const len = Math.max(a.length, b.length);
		if (len < bestLen) {
			best = [a, b];
			bestLen = len;
		}
	}
	return best;
}

/** A display size that fits the longest line in the text column. */
function nameSize(lines: readonly string[]): number {
	const longest = Math.max(...lines.map((l) => l.length));
	if (longest <= 13) return 84;
	if (longest <= 17) return 72;
	if (longest <= 21) return 60;
	return 50;
}

export function shareCardSvg(card: ShareCard): string {
	const { width: W, height: H } = CARD;
	const lines = nameLines(card.eventName);
	const size = nameSize(lines);
	const nameTop = 250 - (lines.length - 1) * size * 0.5;
	const name = lines
		.map(
			(line, i) =>
				`<text x="72" y="${Math.round(nameTop + i * size * 1.02)}" font-family="Bricolage Grotesque" font-weight="600" font-size="${size}" fill="${COLOR.ink}" letter-spacing="-1.5">${escapeXml(line)}</text>`
		)
		.join('');
	const afterName = nameTop + (lines.length - 1) * size * 1.02;
	const track =
		card.outline && card.outline.length > 1 ? outlinePath(card.outline, 470, 430, 40) : '';
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<rect width="${W}" height="${H}" fill="${COLOR.paper}"/>
<rect x="666" y="64" width="470" height="430" rx="22" fill="${COLOR.panel}" stroke="${COLOR.rule}" stroke-width="2"/>
${
	track
		? `<g transform="translate(666 64)"><path d="${track}" fill="none" stroke="${COLOR.track}" stroke-width="16" stroke-linejoin="round"/><path d="${track}" fill="none" stroke="${COLOR.accent}" stroke-width="4" stroke-linejoin="round"/></g>`
		: ''
}
<text x="72" y="128" font-family="Geist Mono" font-weight="500" font-size="22" fill="${COLOR.accent}" letter-spacing="3">ROUND ${card.round} · ${card.season}</text>
${name}
<text x="72" y="${Math.round(afterName + 64)}" font-family="Geist" font-weight="400" font-size="30" fill="${COLOR.ink2}">${escapeXml(card.venue)}</text>
<text x="72" y="${Math.round(afterName + 110)}" font-family="Geist Mono" font-weight="500" font-size="30" fill="${COLOR.ink}">${escapeXml(card.dates)}</text>
<line x1="72" x2="${W - 64}" y1="548" y2="548" stroke="${COLOR.rule}" stroke-width="2"/>
<text x="72" y="590" font-family="Bricolage Grotesque" font-weight="600" font-size="32" fill="${COLOR.ink}">FastPitStop</text>
<text x="${W - 64}" y="590" text-anchor="end" font-family="Geist" font-weight="400" font-size="22" fill="${COLOR.muted}">Live timing held to your TV · unofficial, not associated with Formula 1</text>
</svg>`;
}
