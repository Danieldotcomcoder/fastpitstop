/** Product identity — rename here only. Never hardcode the product name
 * in a component; import from this module. */

export const brand = {
	name: 'FastPitStop',
	shortName: 'FPS',
	tagline: 'F1 analytics you can trust on a delay',
	version: '0.1.0-beta',
	betaLabel: 'beta'
} as const;

export type Brand = typeof brand;
