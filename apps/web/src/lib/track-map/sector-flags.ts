/** Which marshal sectors are under a yellow flag right now.
 *
 * Race control flags a stretch of track by marshal sector: "YELLOW IN TRACK
 * SECTOR 7", "DOUBLE YELLOW IN TRACK SECTOR 7", then "CLEAR IN TRACK SECTOR
 * 7". The circuit data says where each sector is (`marshal_sectors`), so
 * the map can show the stretch itself. A track-wide green or clear, the
 * chequered flag or a red flag ends every sector flag at once (the sector
 * clears are not always sent).
 *
 * Folds only released envelopes (Delay Sync's), cursor-based like every
 * other reducer here. The sector number comes from the event's `sector`,
 * or, for events stored before it was kept (2026-10-30), from the message.
 */
import type { Envelope } from '$lib/delay-sync';

export type SectorFlag = 'yellow' | 'double_yellow';

const SECTOR_IN_MESSAGE = /TRACK SECTOR (\d+)/i;
const TRACK_RESETS: ReadonlySet<string> = new Set(['GREEN', 'CLEAR', 'CHEQUERED', 'RED']);

function sectorOf(payload: Record<string, unknown>): number | null {
	if (typeof payload.sector === 'number') return payload.sector;
	const match =
		typeof payload.message === 'string' ? SECTOR_IN_MESSAGE.exec(payload.message) : null;
	return match ? Number(match[1]) : null;
}

export class SectorFlagTracker {
	private readonly active = new Map<number, SectorFlag>();
	private cursor = 0;

	update(events: readonly Envelope[]): void {
		for (let i = this.cursor; i < events.length; i++) {
			const envelope = events[i];
			if (envelope?.kind === 'race_control_message') this.apply(envelope.payload);
		}
		this.cursor = events.length;
	}

	reset(): void {
		this.active.clear();
		this.cursor = 0;
	}

	/** Sector number -> its flag, for every sector flagged now. */
	get flags(): ReadonlyMap<number, SectorFlag> {
		return this.active;
	}

	private apply(payload: Record<string, unknown>): void {
		const flag = typeof payload.flag === 'string' ? payload.flag.toUpperCase() : null;
		if (flag === null) return;
		const scope = typeof payload.scope === 'string' ? payload.scope.toLowerCase() : null;
		if (scope === 'track' || flag === 'CHEQUERED' || flag === 'RED') {
			if (TRACK_RESETS.has(flag)) this.active.clear();
			return;
		}
		const sector = sectorOf(payload);
		if (sector === null || (scope !== null && scope !== 'sector')) return;
		if (flag === 'YELLOW') this.active.set(sector, 'yellow');
		else if (flag === 'DOUBLE YELLOW') this.active.set(sector, 'double_yellow');
		else if (flag === 'CLEAR' || flag === 'GREEN') this.active.delete(sector);
	}
}

/** Each flagged marshal sector as a stretch of lap: [from, to) fractions,
 * running on to the next sector's start (the last, on to sector 1's). */
export function flaggedStretches(
	flags: ReadonlyMap<number, SectorFlag>,
	sectors: readonly { number: number; start_m: number }[],
	lapLength: number
): { from: number; to: number; flag: SectorFlag }[] {
	if (!sectors.length || lapLength <= 0) return [];
	const ordered = [...sectors].sort((a, b) => a.number - b.number);
	const out: { from: number; to: number; flag: SectorFlag }[] = [];
	ordered.forEach((sector, i) => {
		const flag = flags.get(sector.number);
		if (!flag) return;
		const next = ordered[(i + 1) % ordered.length]!;
		out.push({ from: sector.start_m / lapLength, to: next.start_m / lapLength, flag });
	});
	return out;
}
