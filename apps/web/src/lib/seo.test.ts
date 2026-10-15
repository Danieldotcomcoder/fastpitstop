import { describe, expect, it } from 'vitest';
import {
	renderSeoHead,
	renderSitemap,
	type SeoCircuit,
	type SeoSession,
	serverRendered,
	seoFor,
	sessionForRound,
	SITE_URL,
	sitemapPaths
} from './seo';

const sessions: SeoSession[] = [
	{
		year: 2026,
		round: 16,
		sessionType: 'practice_2',
		eventName: 'Bahrain Grand Prix',
		circuitName: 'Sepang International Circuit',
		live: false
	},
	{
		year: 2026,
		round: 16,
		sessionType: 'qualifying',
		eventName: 'Bahrain Grand Prix',
		circuitName: 'Sepang International Circuit',
		live: true
	},
	{
		year: 2026,
		round: 15,
		sessionType: 'race',
		eventName: 'Azerbaijan Grand Prix',
		circuitName: 'Baku City Circuit',
		live: false
	},
	{
		year: 2026,
		round: 15,
		sessionType: 'practice_1',
		eventName: 'Azerbaijan Grand Prix',
		circuitName: 'Baku City Circuit',
		live: false
	}
];

describe('seoFor', () => {
	it('gives the home page a keyword title, description and structured data', () => {
		const seo = seoFor('/');
		expect(seo.title).toMatch(/F1 Live Timing Dashboard/);
		expect(seo.title.length).toBeLessThanOrEqual(70);
		expect(seo.description.length).toBeGreaterThan(80);
		expect(seo.description.length).toBeLessThanOrEqual(170);
		expect(seo.description).toMatch(/unofficial/i);
		const types = seo.jsonLd.map((item) => item['@type']);
		expect(types).toEqual(['WebSite', 'WebApplication', 'FAQPage']);
		expect(seo.noindex).toBe(false);
	});

	it('names a weekend by its event and venue, never a result', () => {
		const seo = seoFor('/sessions/2026/15', sessions);
		expect(seo.title).toContain('2026 Azerbaijan Grand Prix');
		expect(seo.description).toContain('Baku City Circuit');
		expect(seo.description).toMatch(/no spoilers/i);
		expect(seo.description).not.toMatch(/win|won|victory|podium|P1/);
		expect(seo.jsonLd[0]).toMatchObject({ '@type': 'SportsEvent' });
	});

	it('puts the 2026 Bahrain Grand Prix at Sepang, where it ran', () => {
		const seo = seoFor('/sessions/2026/16', sessions);
		expect(seo.description).toContain('Sepang International Circuit');
		expect(seo.description).not.toContain('Bahrain International Circuit');
	});

	it('says what is live on the live page', () => {
		expect(seoFor('/live', sessions).title).toMatch(/^Live: 2026 Bahrain Grand Prix Qualifying/);
		expect(seoFor('/live', []).title).toMatch(/^F1 Live Timing Dashboard/);
	});

	it('keeps developer harnesses out of the index', () => {
		for (const path of [
			'/race',
			'/analytics',
			'/delay-sync',
			'/track-map',
			'/status',
			'/feedback'
		]) {
			expect(seoFor(path).noindex).toBe(true);
		}
		for (const path of ['/', '/live', '/sessions', '/about', '/sessions/2026/15']) {
			expect(seoFor(path, sessions).noindex).toBe(false);
		}
	});

	it('uses one canonical form without a trailing slash', () => {
		expect(seoFor('/sessions/').path).toBe('/sessions');
		expect(seoFor('/').path).toBe('/');
	});
});

describe('sessionForRound', () => {
	it('prefers the race', () => {
		expect(sessionForRound(sessions, 2026, 15)?.sessionType).toBe('race');
		expect(sessionForRound(sessions, 2026, 16)?.sessionType).toBe('practice_2');
		expect(sessionForRound(sessions, 2026, 99)).toBeNull();
	});
});

describe('renderSeoHead', () => {
	it('escapes text and cannot break out of the JSON-LD script', () => {
		const head = renderSeoHead(
			{
				title: 'A "quoted" <title>',
				description: 'x & y',
				path: '/x',
				noindex: true,
				jsonLd: [{ name: '</script><script>alert(1)</script>' }]
			},
			{ withTitle: true }
		);
		expect(head).toContain('<title>A &quot;quoted&quot; &lt;title&gt;</title>');
		expect(head).toContain('content="x &amp; y"');
		expect(head).toContain('<meta name="robots" content="noindex" />');
		expect(head).not.toContain('</script><script>');
		expect(head).toContain(`<link rel="canonical" href="${SITE_URL}/x" />`);
	});

	it('leaves the title to server-rendered pages', () => {
		const head = renderSeoHead(seoFor('/'), { withTitle: false });
		expect(head).not.toContain('<title>');
		expect(head).toContain('og:title');
	});
});

describe('sitemap', () => {
	it('lists the content pages and one page per weekend, newest first', () => {
		const paths = sitemapPaths(sessions);
		expect(paths).toEqual([
			'/',
			'/live',
			'/sessions',
			'/standings',
			'/circuits',
			'/about',
			'/sessions/2026/16',
			'/sessions/2026/15'
		]);
		const xml = renderSitemap(paths);
		expect(xml).toContain(`<loc>${SITE_URL}/sessions/2026/16</loc>`);
		expect(xml.startsWith('<?xml')).toBe(true);
	});
});

describe('circuit pages', () => {
	const circuits: SeoCircuit[] = [
		{
			id: 'marina_bay',
			name: 'Marina Bay Street Circuit',
			country: 'Singapore',
			pitLossS: 30.13,
			pitLossProvenance: 'calibrated'
		},
		{
			id: 'monaco',
			name: 'Circuit de Monaco',
			country: 'Monaco',
			pitLossS: 19,
			pitLossProvenance: 'provisional'
		}
	];

	it('puts the measured pit loss in the title, and a place in the data', () => {
		const seo = seoFor('/circuits/marina-bay', [], circuits);
		expect(seo.title).toMatch(/^Marina Bay Street Circuit: Pit Stop Time Loss \(30\.1 s\)/);
		expect(seo.description).toContain('measured from real stops');
		expect(seo.noindex).toBe(false);
		expect(JSON.stringify(seo.jsonLd)).toContain('SportsActivityLocation');
	});

	it('never calls a default a measurement', () => {
		expect(seoFor('/circuits/monaco', [], circuits).description).toContain('provisional');
	});

	it('lists every circuit in the sitemap, and renders them on the server', () => {
		const paths = sitemapPaths([], circuits);
		expect(paths).toContain('/circuits/marina-bay');
		expect(paths).toContain('/circuits/monaco');
		expect(serverRendered('/circuits')).toBe(true);
		expect(serverRendered('/circuits/marina-bay')).toBe(true);
		expect(serverRendered('/live')).toBe(false);
	});
});

describe('Grand Prix pages', () => {
	const race = {
		year: 2026,
		round: 18,
		slug: 'united-states',
		eventName: 'United States Grand Prix',
		venue: 'Circuit of the Americas',
		location: 'Austin',
		country: 'United States',
		dates: '23–25 Oct',
		sessions: [
			{
				name: 'Practice 1',
				startUtc: '2026-10-23T17:30:00.000Z',
				endUtc: '2026-10-23T18:30:00.000Z'
			},
			{ name: 'Race', startUtc: '2026-10-25T20:00:00.000Z', endUtc: '2026-10-25T22:00:00.000Z' }
		]
	};

	it('is titled for "<race> start time" searches, with event data', () => {
		const seo = seoFor('/races/2026/united-states', [], [], [race]);
		expect(seo.title).toBe(
			'2026 United States Grand Prix: Schedule, Start Times & Countdown | FastPitStop'
		);
		expect(seo.description).toContain('Circuit of the Americas (23–25 Oct)');
		const [event] = seo.jsonLd as [Record<string, unknown>];
		expect(event['@type']).toBe('SportsEvent');
		expect(event.startDate).toBe('2026-10-23T17:30:00.000Z');
		expect(event.endDate).toBe('2026-10-25T22:00:00.000Z');
		expect(event.subEvent).toHaveLength(2);
	});

	it('names no result, and is in the sitemap', () => {
		const seo = seoFor('/races/2026/united-states', [], [], [race]);
		expect(`${seo.title} ${seo.description}`).not.toMatch(/won|winner|victory|podium/i);
		expect(sitemapPaths([], [], [race])).toContain('/races/2026/united-states');
		expect(serverRendered('/races/2026/united-states')).toBe(true);
	});

	it('shares its own card (S6); other pages keep the site image', () => {
		const head = renderSeoHead(seoFor('/races/2026/united-states', [], [], [race]), {
			withTitle: false
		});
		expect(head).toContain(
			'<meta property="og:image" content="https://fastpitstop.online/races/2026/united-states/og.png" />'
		);
		expect(head).toContain(
			'<meta name="twitter:image" content="https://fastpitstop.online/races/2026/united-states/og.png" />'
		);
		expect(head).toContain('2026 United States Grand Prix, Circuit of the Americas, 23–25 Oct');
		expect(renderSeoHead(seoFor('/about'), { withTitle: false })).toContain(
			'content="https://fastpitstop.online/og-image.png"'
		);
	});
});

describe('standings', () => {
	it('is titled for the season, spoiler-free, and rendered on the server', () => {
		const seo = seoFor('/standings/2024');
		expect(seo.title).toBe(
			'F1 2024 Standings — Drivers & Constructors, Spoiler-Free | FastPitStop'
		);
		expect(seo.description).not.toMatch(/won|winner|leads|champion\b/i);
		expect(serverRendered('/standings')).toBe(true);
		expect(serverRendered('/standings/2024')).toBe(true);
	});
});
