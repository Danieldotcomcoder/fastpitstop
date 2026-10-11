/** WCAG AA contrast for the design tokens, in both themes (ROADMAP W2 "done
 * when"). Reads tokens.css itself, so a token change that drops a text
 * colour below 4.5:1 on any surface it sits on fails here. */
import { describe, expect, it } from 'vitest';
import css from '../styles/tokens.css?raw';

function block(selector: RegExp): Record<string, string> {
	const match = selector.exec(css);
	if (!match) throw new Error(`no block for ${selector}`);
	const start = css.indexOf('{', match.index) + 1;
	let depth = 1;
	let i = start;
	while (depth > 0 && i < css.length) {
		if (css[i] === '{') depth++;
		if (css[i] === '}') depth--;
		i++;
	}
	const out: Record<string, string> = {};
	for (const m of css.slice(start, i - 1).matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
		out[m[1]!] = m[2]!.trim();
	}
	return out;
}

const light = block(/^:root\s*\{/m);
const dark = { ...light, ...block(/^:root:not\(\[data-theme='light'\]\)\s*\{/m) };

function resolve(theme: Record<string, string>, name: string, seen = new Set<string>()): string {
	const raw = theme[name];
	if (raw === undefined) throw new Error(`unknown token ${name}`);
	const ref = /^var\((--[\w-]+)\)$/.exec(raw);
	if (!ref) return raw;
	if (seen.has(name)) throw new Error(`cycle at ${name}`);
	seen.add(name);
	return resolve(theme, ref[1]!, seen);
}

function luminance(hex: string): number {
	const m = /^#([0-9a-f]{6})$/i.exec(hex);
	if (!m) throw new Error(`not a hex colour: ${hex}`);
	const [r, g, b] = [0, 2, 4].map((i) => {
		const c = parseInt(m[1]!.slice(i, i + 2), 16) / 255;
		return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
	}) as [number, number, number];
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
	const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p) as [number, number];
	return (x + 0.05) / (y + 0.05);
}

const SURFACES = ['--paper', '--panel', '--panel-sunk'];
const TEXT = ['--ink', '--ink-2', '--muted', '--accent', '--fastest', '--pb', '--flag-red'];
/** Text set on a filled object, with the fill it sits on. */
const ON_FILL: [string, string][] = [
	['--status-yellow-ink', '--status-yellow'],
	['--status-sc-ink', '--status-sc'],
	['--status-vsc-ink', '--status-vsc'],
	['--status-red-ink', '--status-red'],
	['--status-chequered-ink', '--status-chequered'],
	['--board-ink', '--board'],
	['--board-muted', '--board'],
	['--board-hi', '--board'],
	['--accent-ink', '--accent']
];

describe.each([
	['light', light],
	['dark', dark]
])('%s theme', (_name, theme) => {
	const pairs: [string, string][] = [
		...SURFACES.flatMap((surface) => TEXT.map((text): [string, string] => [text, surface])),
		...ON_FILL
	];
	it.each(pairs)('%s on %s meets AA (4.5:1)', (text, surface) => {
		const value = ratio(resolve(theme, text), resolve(theme, surface));
		expect(value, `${text} on ${surface}: ${value.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
	});
});

describe('the contrast check itself', () => {
	it('matches known WCAG ratios', () => {
		expect(ratio('#000000', '#ffffff')).toBeCloseTo(21, 5);
		// #777 on white is the classic just-below-AA grey.
		expect(ratio('#777777', '#ffffff')).toBeLessThan(4.5);
		expect(ratio('#767676', '#ffffff')).toBeGreaterThanOrEqual(4.5);
	});
});
