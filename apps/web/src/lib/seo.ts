/** What search engines and link previews see for each page: title,
 * description, canonical URL, social cards and structured data.
 *
 * The race dashboards (`/live`, `/sessions/{year}/{round}`) render in the
 * browser, so on their own they hand a crawler an empty page. The server
 * hook (`hooks.server.ts`) writes this module's tags into every page's HTML
 * instead, and the content pages that are rendered on the server (`/`,
 * `/about`, `/sessions`, the circuit pages) take their `<title>` from here
 * too, so there is one source for both.
 *
 * Nothing here may spoil a race: descriptions name the event and what can
 * be explored, never a result. And nothing may read as official: the
 * product is described as unofficial wherever it describes itself. */

import type { components } from './api/schema';
import { brand } from './brand';

export const SITE_URL = 'https://fastpitstop.online';
export const OG_IMAGE = { path: '/og-image.png', width: 1200, height: 630 } as const;

/** The routes rendered on the server, which output their own `<title>`. */
export const SERVER_RENDERED = new Set(['/', '/about', '/sessions', '/circuits']);

export function serverRendered(path: string): boolean {
	return (
		SERVER_RENDERED.has(path) ||
		path === '/standings' ||
		['/circuits/', '/races/', '/standings/'].some((prefix) => path.startsWith(prefix))
	);
}

/** Developer harnesses and utility pages: reachable, never indexed. */
const NOINDEX_PREFIXES = [
	'/race',
	'/analytics',
	'/delay-sync',
	'/design',
	'/track-map',
	'/status',
	'/feedback',
	'/radio'
];

export interface SeoSession {
	year: number;
	round: number;
	sessionType: string;
	eventName: string | null;
	circuitName: string | null;
	live: boolean;
}

/** A Grand Prix weekend, as this module needs it (`GET /schedule`). */
export interface SeoRace {
	year: number;
	round: number;
	slug: string;
	eventName: string;
	/** The circuit, or the town when no circuit file knows the venue. */
	venue: string;
	location: string;
	country: string;
	/** "23–25 Oct", at the track. */
	dates: string;
	sessions: { name: string; startUtc: string; endUtc: string }[];
}

/** A circuit, as this module needs it (`GET /circuits`). */
export interface SeoCircuit {
	id: string;
	name: string;
	country: string;
	pitLossS: number;
	pitLossProvenance: string;
}

export function toSeoCircuit(row: {
	circuit_id: string;
	name: string;
	country: string;
	pit_loss_s: number;
	pit_loss_provenance: string;
}): SeoCircuit {
	return {
		id: row.circuit_id,
		name: row.name,
		country: row.country,
		pitLossS: row.pit_loss_s,
		pitLossProvenance: row.pit_loss_provenance
	};
}

/** A row of `GET /sessions`, as this module needs it. */
export function toSeoSession(row: components['schemas']['SessionSummaryOut']): SeoSession {
	return {
		year: row.session_key.year,
		round: row.session_key.round,
		sessionType: row.session_key.session_type,
		eventName: row.event_name,
		circuitName: row.circuit_name ?? null,
		live: row.live === true
	};
}

export interface PageSeo {
	title: string;
	description: string;
	/** Canonical path, without the origin. */
	path: string;
	noindex: boolean;
	jsonLd: Record<string, unknown>[];
	/** The page's own share image; the site's default when absent. */
	image?: { path: string; alt: string };
}

export const HOME_FAQ: readonly { question: string; answer: string }[] = [
	{
		question: 'What is FastPitStop?',
		answer:
			'An unofficial, free F1 live timing dashboard. Alongside the running order and gaps it works out what the timing feed does not say: how fast each tyre is wearing, whether an undercut will work, and why a driver suddenly gained or lost time.'
	},
	{
		question: 'How does it avoid spoiling the race?',
		answer:
			'Set your delay once, or tap when the lights go out, and every number, chart and prediction waits until your TV or stream has shown that moment. The dashboard corrects for a wrong clock on your phone or computer too.'
	},
	{
		question: 'Can I watch a race again later?',
		answer:
			'Yes. Past races replay lap by lap through the same dashboard at any speed, with the same spoiler protection, so you can rewatch a Grand Prix on your own schedule.'
	},
	{
		question: 'Is FastPitStop official?',
		answer:
			'No. It is an unofficial fan project and is not associated with Formula 1, the FIA or any team. It uses no official logos.'
	}
];

export const SESSION_TYPE_LABELS: Record<string, string> = {
	race: 'Race',
	sprint: 'Sprint',
	qualifying: 'Qualifying',
	sprint_qualifying: 'Sprint Qualifying',
	practice_1: 'Practice 1',
	practice_2: 'Practice 2',
	practice_3: 'Practice 3'
};

function eventTitle(year: number, eventName: string | null, round: number): string {
	return eventName ? `${year} ${eventName}` : `${year} Round ${round}`;
}

/** The session a round's page shows: the race when there is one. */
export function sessionForRound(
	sessions: readonly SeoSession[],
	year: number,
	round: number
): SeoSession | null {
	const matches = sessions.filter((s) => s.year === year && s.round === round);
	return matches.find((s) => s.sessionType === 'race') ?? matches[0] ?? null;
}

export function seoFor(
	pathname: string,
	sessions: readonly SeoSession[] = [],
	circuits: readonly SeoCircuit[] = [],
	races: readonly SeoRace[] = []
): PageSeo {
	const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
	const page = (title: string, description: string, jsonLd: Record<string, unknown>[] = []) => ({
		title,
		description,
		path,
		noindex: NOINDEX_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`)),
		jsonLd
	});

	if (path === '/') {
		return page(
			`${brand.name} — F1 Live Timing Dashboard & Race Strategy`,
			'Free, unofficial F1 live timing dashboard: gaps, tyre degradation, undercut predictions and pit strategy, held back to your TV delay so the race is never spoiled.',
			homeJsonLd()
		);
	}
	if (path === '/live') {
		const live = sessions.find((s) => s.live) ?? null;
		const now = live
			? `${eventTitle(live.year, live.eventName, live.round)} ${SESSION_TYPE_LABELS[live.sessionType] ?? ''}`.trim()
			: null;
		return page(
			now
				? `Live: ${now} — F1 Live Timing | ${brand.name}`
				: `F1 Live Timing Dashboard | ${brand.name}`,
			'Follow the F1 session as it happens: running order, gaps, tyre ages, pit stops, race control and an undercut predictor, synced to your broadcast delay so nothing is spoiled.'
		);
	}
	if (path === '/sessions') {
		const years = [...new Set(sessions.map((s) => s.year))].sort();
		const span = years.length > 1 ? `${years[0]}–${years[years.length - 1]}` : (years[0] ?? '');
		return page(
			`F1 Race Replays${span ? ` ${span}` : ''} — Spoiler-Free | ${brand.name}`,
			'Replay past Formula 1 races lap by lap: live timing, tyre strategy, pit stops and undercut calls, revealed only as each race unfolds. No results shown in advance.'
		);
	}
	if (path === '/about') {
		return page(
			`About ${brand.name} — Unofficial F1 Analytics Dashboard`,
			`What ${brand.name} is, how its tyre, pit-stop and undercut analysis works, and how it keeps a delayed broadcast spoiler-free. An unofficial fan project.`
		);
	}
	const standings = /^\/standings(?:\/(\d{4}))?$/.exec(path);
	if (standings) {
		const year = standings[1] ?? String(new Date().getUTCFullYear());
		return page(
			`F1 ${year} Standings — Drivers & Constructors, Spoiler-Free | ${brand.name}`,
			`The ${year} Formula 1 drivers' and constructors' championships after the last race you watched, with each driver's points race by race and the points still available. Later races stay hidden.`
		);
	}
	if (path === '/circuits') {
		return page(
			`F1 Circuits — Pit Stop Time Loss, Tyre Wear & Track Maps | ${brand.name}`,
			'Every Formula 1 circuit measured from real races: what a pit stop costs, how fast each tyre compound wears, the corners and the DRS zones.'
		);
	}
	const circuitPath = /^\/circuits\/([a-z0-9-]+)$/.exec(path);
	if (circuitPath) {
		const circuit = circuits.find((c) => c.id === circuitPath[1]!.replace(/-/g, '_'));
		if (circuit) {
			const loss = `${circuit.pitLossS.toFixed(1)} s`;
			return page(
				`${circuit.name}: Pit Stop Time Loss (${loss}), Tyre Wear & Map | ${brand.name}`,
				`What a pit stop costs at the ${circuit.name} (${loss}, ${circuit.pitLossProvenance === 'calibrated' ? 'measured from real stops' : 'provisional'}), how fast each tyre compound wears there, its corners and DRS zones.`,
				[
					{
						'@context': 'https://schema.org',
						'@type': 'SportsActivityLocation',
						name: circuit.name,
						address: { '@type': 'PostalAddress', addressCountry: circuit.country },
						url: `${SITE_URL}${path}`
					}
				]
			);
		}
	}
	const racePath = /^\/races\/(\d{4})\/([a-z0-9-]+)$/.exec(path);
	if (racePath) {
		const race = races.find((r) => r.year === Number(racePath[1]) && r.slug === racePath[2]);
		if (race) {
			const name = `${race.year} ${race.eventName}`;
			return {
				...page(
					`${name}: Schedule, Start Times & Countdown | ${brand.name}`,
					`Every session of the ${name} at ${race.venue} (${race.dates}) in your own time zone, with a countdown, what a pit stop costs there and how the last race was run. Spoiler-free.`,
					[raceJsonLd(race, path)]
				),
				// Its own card (ROADMAP S6): the circuit, name and dates.
				image: {
					path: `${path}/og.png`,
					alt: `${name}, ${race.venue}, ${race.dates}: the circuit outline`
				}
			};
		}
	}
	const ledger = /^\/sessions\/(\d{4})\/(\d{1,2})\/calls$/.exec(path);
	if (ledger) {
		const year = Number(ledger[1]);
		const roundNo = Number(ledger[2]);
		const session = sessionForRound(sessions, year, roundNo);
		const name = eventTitle(year, session?.eventName ?? null, roundNo);
		// Only reachable while a call kind is served (ROADMAP W5): not for
		// search engines until one has passed its held-out backtest.
		return {
			...page(
				`Our calls in the ${name} | ${brand.name}`,
				`Every strategy call we made in the ${name}, settled as right, wrong or not counted, held back to the lap you have watched.`
			),
			noindex: true
		};
	}
	const round = /^\/sessions\/(\d{4})\/(\d{1,2})$/.exec(path);
	if (round) {
		const year = Number(round[1]);
		const roundNo = Number(round[2]);
		const session = sessionForRound(sessions, year, roundNo);
		const name = eventTitle(year, session?.eventName ?? null, roundNo);
		const venue = session?.circuitName ? ` at ${session.circuitName}` : '';
		return page(
			`${name} Replay — Timing, Tyres & Strategy | ${brand.name}`,
			`Replay the ${name}${venue} lap by lap: live timing, tyre degradation, pit stops and undercut calls, revealed only as the race unfolds. No spoilers.`,
			session ? [eventJsonLd(session, path)] : []
		);
	}
	return page(brand.name, `${brand.name} — ${brand.tagline}. Unofficial fan project.`);
}

function homeJsonLd(): Record<string, unknown>[] {
	return [
		{
			'@context': 'https://schema.org',
			'@type': 'WebSite',
			name: brand.name,
			url: `${SITE_URL}/`
		},
		{
			'@context': 'https://schema.org',
			'@type': 'WebApplication',
			name: brand.name,
			url: `${SITE_URL}/`,
			applicationCategory: 'SportsApplication',
			operatingSystem: 'Any (web browser)',
			isAccessibleForFree: true,
			offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
			description:
				'Unofficial F1 live timing dashboard with tyre degradation, undercut predictions and pit strategy, synced to your broadcast delay.'
		},
		{
			'@context': 'https://schema.org',
			'@type': 'FAQPage',
			mainEntity: HOME_FAQ.map((item) => ({
				'@type': 'Question',
				name: item.question,
				acceptedAnswer: { '@type': 'Answer', text: item.answer }
			}))
		}
	];
}

/** A Grand Prix weekend as a schema.org event, its sessions as sub-events:
 * the start dates are what makes it eligible for event rich results. */
function raceJsonLd(race: SeoRace, path: string): Record<string, unknown> {
	const place = {
		'@type': 'Place',
		name: race.venue,
		address: {
			'@type': 'PostalAddress',
			addressLocality: race.location,
			addressCountry: race.country
		}
	};
	const first = race.sessions[0];
	const last = race.sessions.at(-1);
	return {
		'@context': 'https://schema.org',
		'@type': 'SportsEvent',
		name: `${race.year} ${race.eventName}`,
		sport: 'Motor racing',
		url: `${SITE_URL}${path}`,
		image: `${SITE_URL}${OG_IMAGE.path}`,
		eventStatus: 'https://schema.org/EventScheduled',
		eventAttendanceMode: 'https://schema.org/OfflineEventAttendanceMode',
		...(first ? { startDate: first.startUtc } : {}),
		...(last ? { endDate: last.endUtc } : {}),
		location: place,
		subEvent: race.sessions.map((s) => ({
			'@type': 'SportsEvent',
			name: `${race.year} ${race.eventName}: ${s.name}`,
			startDate: s.startUtc,
			endDate: s.endUtc,
			location: place
		}))
	};
}

function eventJsonLd(session: SeoSession, path: string): Record<string, unknown> {
	const event: Record<string, unknown> = {
		'@context': 'https://schema.org',
		'@type': 'SportsEvent',
		name: eventTitle(session.year, session.eventName, session.round),
		sport: 'Motor racing',
		url: `${SITE_URL}${path}`
	};
	if (session.circuitName) {
		event.location = { '@type': 'Place', name: session.circuitName };
	}
	return event;
}

function escapeHtml(value: string): string {
	return value
		.replaceAll('&', '&amp;')
		.replaceAll('"', '&quot;')
		.replaceAll('<', '&lt;')
		.replaceAll('>', '&gt;');
}

/** `<script type="application/ld+json">` content may not contain `</`. */
function jsonForScript(value: unknown): string {
	return JSON.stringify(value).replaceAll('<', '\\u003c');
}

/** The tags for one page. `withTitle` is false for server-rendered pages,
 * whose own `<svelte:head>` outputs the (same) title. */
export function renderSeoHead(seo: PageSeo, { withTitle }: { withTitle: boolean }): string {
	const url = `${SITE_URL}${seo.path}`;
	const image = `${SITE_URL}${seo.image?.path ?? OG_IMAGE.path}`;
	const imageAlt = seo.image?.alt ?? `${brand.name} — unofficial F1 live timing dashboard`;
	const title = escapeHtml(seo.title);
	const description = escapeHtml(seo.description);
	const tags = [
		withTitle ? `<title>${title}</title>` : '',
		`<meta name="description" content="${description}" />`,
		`<link rel="canonical" href="${url}" />`,
		seo.noindex ? '<meta name="robots" content="noindex" />' : '',
		`<meta property="og:site_name" content="${escapeHtml(brand.name)}" />`,
		'<meta property="og:type" content="website" />',
		`<meta property="og:title" content="${title}" />`,
		`<meta property="og:description" content="${description}" />`,
		`<meta property="og:url" content="${url}" />`,
		`<meta property="og:image" content="${image}" />`,
		`<meta property="og:image:width" content="${OG_IMAGE.width}" />`,
		`<meta property="og:image:height" content="${OG_IMAGE.height}" />`,
		`<meta property="og:image:alt" content="${escapeHtml(imageAlt)}" />`,
		'<meta name="twitter:card" content="summary_large_image" />',
		`<meta name="twitter:title" content="${title}" />`,
		`<meta name="twitter:description" content="${description}" />`,
		`<meta name="twitter:image" content="${image}" />`,
		...seo.jsonLd.map(
			(item) => `<script type="application/ld+json">${jsonForScript(item)}</script>`
		)
	];
	return tags.filter(Boolean).join('\n\t\t');
}

/** Every URL worth indexing, for `sitemap.xml`. */
export function sitemapPaths(
	sessions: readonly SeoSession[],
	circuits: readonly SeoCircuit[] = [],
	races: readonly SeoRace[] = []
): string[] {
	const rounds = new Map<string, SeoSession>();
	for (const s of sessions) rounds.set(`${s.year}/${s.round}`, s);
	const sessionPaths = [...rounds.values()]
		.sort((a, b) => b.year - a.year || b.round - a.round)
		.map((s) => `/sessions/${s.year}/${s.round}`);
	const circuitPaths = circuits.map((c) => `/circuits/${c.id.replace(/_/g, '-')}`);
	const racePaths = races.map((r) => `/races/${r.year}/${r.slug}`);
	return [
		'/',
		'/live',
		'/sessions',
		'/standings',
		'/circuits',
		'/about',
		...racePaths,
		...sessionPaths,
		...circuitPaths
	];
}

export function renderSitemap(paths: readonly string[]): string {
	const urls = paths
		.map((path) => `  <url><loc>${escapeHtml(`${SITE_URL}${path}`)}</loc></url>`)
		.join('\n');
	return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
}
