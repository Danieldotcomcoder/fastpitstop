/** Categorical colour for comparing several drivers on one retrospective
 * chart (race shape, stints). Deliberately NOT team colour: `/laps` (a
 * historical REST read, unlike the live `driver_list_updated` domain event
 * `$lib/timing-tower` consumes) carries no team name at all — this package
 * has no driver/team roster to join against. Deliberately NOT drawn from the
 * reserved timing/tyre hues either (purple/green/yellow/red already mean
 * something specific per CLAUDE.md's colour thesis) — a cycling categorical
 * set so multiple drivers stay visually distinct without borrowing a
 * meaning that isn't theirs. */
const DRIVER_PALETTE: readonly string[] = [
	'#4FC3F7',
	'#FFA94D',
	'#FF6FA5',
	'#59D6C4',
	'#9FA8FF',
	'#D4A373',
	'#7FD1E8',
	'#F2A6C0',
	'#8FBF6F',
	'#C9A6FF'
];

export function colorForDriverIndex(index: number): string {
	return DRIVER_PALETTE[index % DRIVER_PALETTE.length] as string;
}
