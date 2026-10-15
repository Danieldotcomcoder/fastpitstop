import { describe, expect, it } from 'vitest';
import { renderShareCard } from './render';

/** Width and height from a PNG's IHDR chunk. */
function pngSize(png: Uint8Array): [number, number] {
	const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
	return [view.getUint32(16), view.getUint32(20)];
}

describe('renderShareCard', () => {
	it('renders a 1200x630 PNG with the bundled fonts, and reuses it', () => {
		const card = {
			season: 2026,
			round: 19,
			eventName: 'United States Grand Prix',
			venue: 'Circuit of the Americas',
			dates: '23–25 Oct',
			outline: null
		};
		const png = renderShareCard(card);
		expect([...png.subarray(1, 4)].map((c) => String.fromCharCode(c)).join('')).toBe('PNG');
		expect(pngSize(png)).toEqual([1200, 630]);
		expect(renderShareCard(card)).toBe(png);
		if (process.env.OG_PREVIEW) {
			// Visual check while developing: OG_PREVIEW=/path/card.png
			void import('node:fs').then((fs) => fs.writeFileSync(process.env.OG_PREVIEW!, png));
		}
	});
});
