/** Shared chart helpers for the desk's analytics tabs, ported from the
 * supplied design: "nice" axis ticks, and the focused pair's two series
 * colours. */
import { colorForTeam } from '$lib/teams';

/** Round-number ticks spanning [lo, hi], about `n` of them. */
export function niceTicks(lo: number, hi: number, n = 5): number[] {
	const span = hi - lo;
	if (!(span > 0)) return [lo];
	const step0 = span / n;
	const mag = Math.pow(10, Math.floor(Math.log10(step0)));
	const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? mag * 10;
	const out: number[] = [];
	for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
	return out;
}

function oklab(hex: string): [number, number, number] | null {
	const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
	if (!match) return null;
	const value = match[1]!;
	const lin = (c: number) => {
		const x = c / 255;
		return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
	};
	const [r, g, b] = [0, 2, 4].map((i) => lin(parseInt(value.slice(i, i + 2), 16))) as [
		number,
		number,
		number
	];
	const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
	];
}

/** The attacker and defender series colours: team colours, unless the two
 * are too alike to tell apart (teammates), when the defender takes ink. */
export function pairColors(teamA: string | null, teamB: string | null): { a: string; b: string } {
	const a = colorForTeam(teamA);
	const b = colorForTeam(teamB);
	const x = oklab(a);
	const y = oklab(b);
	if (x && y) {
		const distance = 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
		if (distance < 20) return { a, b: 'var(--ink-2)' };
	}
	return { a, b };
}

/** A Svelte action reporting an element's width, for charts drawn in
 * viewBox units that should match the space they are given. */
export function measureWidth(node: HTMLElement, onWidth: (width: number) => void) {
	const observer = new ResizeObserver((entries) => {
		const width = entries[0]?.contentRect.width;
		if (width) onWidth(Math.max(290, Math.round(width)));
	});
	observer.observe(node);
	return { destroy: () => observer.disconnect() };
}
