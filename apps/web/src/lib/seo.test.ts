import { describe, expect, it } from 'vitest';
import {
	renderSeoHead,
	renderSitemap,
	type SeoSession,
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
			'/about',
			'/sessions/2026/16',
			'/sessions/2026/15'
		]);
		const xml = renderSitemap(paths);
		expect(xml).toContain(`<loc>${SITE_URL}/sessions/2026/16</loc>`);
		expect(xml.startsWith('<?xml')).toBe(true);
	});
});
