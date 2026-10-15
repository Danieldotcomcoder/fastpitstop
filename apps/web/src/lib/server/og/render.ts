/** Turns a share card (`$lib/og/card`) into the PNG link previews need:
 * X, Facebook, WhatsApp and iMessage show no SVG. Rendered by resvg with
 * the site's own faces bundled into the server (`./fonts`, SIL Open Font
 * License, licences beside them), never system fonts, so a card looks the
 * same on every machine. Each card is rendered once per process and kept:
 * its inputs (the calendar, a circuit file) change a few times a year. */
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Resvg } from '@resvg/resvg-js';
import { CARD, shareCardSvg, type ShareCard } from '$lib/og/card';
import bricolage600 from './fonts/BricolageGrotesque-600.ttf?inline';
import geist400 from './fonts/Geist-400.ttf?inline';
import geist600 from './fonts/Geist-600.ttf?inline';
import geistMono500 from './fonts/GeistMono-500.ttf?inline';

/** resvg 2.6 loads fonts from files only, so the bundled faces are written
 * out once per process, wherever the build put the server. */
let fontFiles: string[] | null = null;
function fonts(): string[] {
	if (fontFiles) return fontFiles;
	const dir = mkdtempSync(join(tmpdir(), 'fps-og-fonts-'));
	fontFiles = Object.entries({ bricolage600, geist400, geist600, geistMono500 }).map(
		([name, dataUrl]) => {
			const file = join(dir, `${name}.ttf`);
			writeFileSync(file, Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64'));
			return file;
		}
	);
	return fontFiles;
}

/** Bounded: one entry per Grand Prix asked for. */
const cache = new Map<string, Buffer>();
const MAX_CACHED = 200;

export function renderShareCard(card: ShareCard): Buffer {
	const svg = shareCardSvg(card);
	const hit = cache.get(svg);
	if (hit) return hit;
	const png = new Resvg(svg, {
		fitTo: { mode: 'width', value: CARD.width },
		font: { fontFiles: fonts(), loadSystemFonts: false, defaultFontFamily: 'Geist' }
	})
		.render()
		.asPng();
	if (cache.size >= MAX_CACHED) cache.delete(cache.keys().next().value as string);
	cache.set(svg, png);
	return png;
}
