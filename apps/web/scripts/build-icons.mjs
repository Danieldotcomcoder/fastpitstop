// The site's icons, rendered from the one source, `static/favicon.svg`:
//   node scripts/build-icons.mjs
// Search engines read these: Google shows a square icon in multiples of
// 48 px (it is offered 48 and 96), and Bing, like many other clients, asks
// for `/favicon.ico` by name (16, 32 and 48 px inside, each a PNG). The
// .ico is served by `src/routes/favicon.ico/+server.ts`, not from static/:
// adapter-node's file server knows no type for .ico and sent none. The
// 192 and 512 px app icons and the apple-touch icon come from the same
// mark and are left as they are.
import { Resvg } from '@resvg/resvg-js';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STATIC = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
const svg = readFileSync(join(STATIC, 'favicon.svg'), 'utf8');

function png(size) {
	return new Resvg(svg, { fitTo: { mode: 'width', value: size } }).render().asPng();
}

for (const size of [48, 96]) writeFileSync(join(STATIC, `icon-${size}.png`), png(size));

/** An ICO holding each size as an embedded PNG (allowed since Windows Vista). */
function ico(sizes) {
	const images = sizes.map((size) => ({ size, data: png(size) }));
	const header = Buffer.alloc(6);
	header.writeUInt16LE(0, 0); // reserved
	header.writeUInt16LE(1, 2); // type: icon
	header.writeUInt16LE(images.length, 4);
	const entries = [];
	let offset = 6 + 16 * images.length;
	for (const { size, data } of images) {
		const entry = Buffer.alloc(16);
		entry.writeUInt8(size >= 256 ? 0 : size, 0); // width
		entry.writeUInt8(size >= 256 ? 0 : size, 1); // height
		entry.writeUInt8(0, 2); // palette colours
		entry.writeUInt8(0, 3); // reserved
		entry.writeUInt16LE(1, 4); // colour planes
		entry.writeUInt16LE(32, 6); // bits per pixel
		entry.writeUInt32LE(data.length, 8);
		entry.writeUInt32LE(offset, 12);
		entries.push(entry);
		offset += data.length;
	}
	return Buffer.concat([header, ...entries, ...images.map((image) => image.data)]);
}

const SERVER = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'lib', 'server');
writeFileSync(join(SERVER, 'favicon.ico'), ico([16, 32, 48]));
console.log('wrote icon-48.png, icon-96.png and src/lib/server/favicon.ico');
