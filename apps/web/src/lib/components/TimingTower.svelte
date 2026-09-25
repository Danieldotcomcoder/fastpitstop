<script lang="ts">
	/** The anchor view of the race dashboard (Phase 3.6): classification
	 * position, gaps, current lap and tyre for every driver, folded entirely
	 * from `controller.visibleState` — the per-domain-event reducer
	 * `$lib/delay-sync/visible-state.ts` explicitly left to "the actual race
	 * views' job" (`$lib/timing-tower`). No REST call and no separate reveal
	 * gate: every field here comes from an envelope Delay Sync has already
	 * released, the same reasoning `GapStreamGraph` gives for skipping
	 * `RevealTracker`. */
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { TimingTowerReducer, positionsGained, type DriverRow } from '$lib/timing-tower';
	import { driverName, lapTime } from '$lib/drivers';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';

	interface Props {
		controller: DelaySyncController;
		year?: number;
	}

	let { controller, year }: Props = $props();

	const reducer = new TimingTowerReducer();

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	$effect(() => {
		// Reset before publishing the new controller's state so the derived
		// rows cannot retain the previous replay during the empty start window.
		reducer.reset();
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	let rows = $derived.by((): DriverRow[] => {
		reducer.update(visible.events);
		// `TimingTowerReducer` mutates each DriverRow in place and hands back
		// the SAME object reference across calls (by design — see its own
		// docstring). Svelte 5's keyed `{#each}` only re-renders a row's bound
		// text when the row VALUE at that key changes identity; a mutated-in-
		// place object never does, so every cell silently froze at whatever it
		// showed the instant that row was first created and never updated
		// again for the rest of a real (non-instant) session — found by
		// driving a real race through this view and watching gap/lap/position
		// values in the underlying reducer state visibly change while the
		// rendered table never moved. A shallow copy per row, every
		// derivation, is what actually gives Svelte a new reference to react
		// to; cheap at ~20 rows of flat primitive fields.
		return reducer.rows.map((row) => ({ ...row }));
	});

	const COMPOUND_LETTER: Record<string, string> = {
		soft: 'S',
		medium: 'M',
		hard: 'H',
		intermediate: 'I',
		wet: 'W'
	};

	function compoundLetter(compound: string | null): string {
		if (compound === null) return '?';
		return COMPOUND_LETTER[compound] ?? '?';
	}

	function fmtGap(row: DriverRow): string {
		if (row.position === 1) return 'LEADER';
		if (row.lapsDown !== null) return `+${row.lapsDown}L`;
		if (row.gapToLeaderS !== null) return `+${row.gapToLeaderS.toFixed(3)}`;
		return '—';
	}

	function fmtInterval(row: DriverRow): string {
		if (row.position === 1) return '—';
		if (row.lapsDown !== null) return '—';
		if (row.intervalToAheadS !== null) return `+${row.intervalToAheadS.toFixed(3)}`;
		return '—';
	}

	function fmtLapTime(row: DriverRow): string {
		return lapTime(row.lastLapTimeS);
	}

	/** Places gained/lost since this driver was first placed. */
	const gained = positionsGained;

	function rowKey(row: DriverRow): string {
		return row.driver;
	}
</script>

<Panel
	title="Timing tower"
	subtitle="Classification, gaps and tyres — released lap by lap, never ahead of your delay"
>
	{#if rows.length === 0}
		<LoadingState label="Waiting for the driver list…" />
	{:else}
		<div class="tower-scroll">
			<table class="tower" data-testid="timing-tower">
				<thead>
					<tr>
						<th class="col-pos">Pos</th>
						<th class="col-driver">Driver</th>
						<th class="col-gap">Gap</th>
						<th class="col-interval">Interval</th>
						<th class="col-lap">Last lap</th>
						<th class="col-tyre">Tyre</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as row (rowKey(row))}
						<tr data-testid="tower-row" data-driver={row.driver} class:in-pit={row.inPit}>
							<td class="col-pos font-mono">
								{row.position ?? '—'}
								{#if gained(row) !== null && gained(row) !== 0}
									<span
										class="delta"
										class:up={(gained(row) ?? 0) > 0}
										title="{Math.abs(gained(row) ?? 0)} place{Math.abs(gained(row) ?? 0) === 1
											? ''
											: 's'} {(gained(row) ?? 0) > 0 ? 'gained' : 'lost'} since the start"
										>{(gained(row) ?? 0) > 0 ? '▲' : '▼'}{Math.abs(gained(row) ?? 0)}</span
									>
								{/if}
							</td>
							<td class="col-driver">
								<span class="code">{driverName(row.driver, row.name, row.code, year)}</span>
								<small class="driver-team">{row.team ?? row.code ?? `#${row.driver}`}</small>
								{#if row.inPit}<span class="pit-badge">PIT</span>{/if}
							</td>
							<td class="col-gap font-mono">{fmtGap(row)}</td>
							<td class="col-interval font-mono">{fmtInterval(row)}</td>
							<td class="col-lap font-mono" class:pb={row.isPersonalBest === true}
								>{fmtLapTime(row)}</td
							>
							<td class="col-tyre">
								<span class="compound compound-{row.compound ?? 'unknown'}"
									>{compoundLetter(row.compound)}</span
								>
								<span class="font-mono tyre-age">{row.tyreAge ?? '—'}</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Panel>

<style>
	.delta {
		font-size: 0.6rem;
		margin-left: 0.25rem;
		color: var(--flag-red);
		letter-spacing: -0.02em;
	}

	.delta.up {
		color: var(--timing-green);
	}

	.driver-team {
		display: block;
		color: var(--text-secondary);
		font: 0.75rem var(--font-ui);
	}
	.col-driver .code {
		font-family: var(--font-ui);
		font-weight: 600;
	}
	.tower-scroll {
		overflow-x: auto;
	}

	.tower {
		width: 100%;
		min-width: 30rem;
		border-collapse: collapse;
		font-size: var(--font-size-sm);
	}

	.tower th {
		text-align: left;
		padding: var(--space-2) var(--space-2);
		font-size: var(--font-size-xs);
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--color-text-muted);
		border-bottom: 1px solid var(--color-border-strong);
		white-space: nowrap;
	}

	.tower td {
		/* Note: inside the race dashboard's timing rail this is overridden by
		   `.timing-rail :global(.tower td)` in RaceWorkspace.svelte, which is
		   where the dense row height actually lives. */
		padding: var(--space-2) var(--space-2);
		border-bottom: 1px solid var(--color-border);
		white-space: nowrap;
	}

	.tower tbody tr.in-pit {
		background: color-mix(in srgb, var(--color-confidence-thin) 10%, transparent);
	}

	.col-pos {
		width: 2.5rem;
		font-weight: 600;
	}

	.col-driver {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.code {
		font-weight: 700;
		letter-spacing: 0.02em;
	}

	.pit-badge {
		padding: 0 var(--space-2);
		border-radius: 999px;
		background: var(--color-confidence-thin);
		color: var(--color-bg);
		font-size: 0.625rem;
		font-weight: 700;
		letter-spacing: 0.04em;
	}

	.col-lap.pb {
		color: var(--color-positive);
		font-weight: 600;
	}

	.col-tyre {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.compound {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.25rem;
		height: 1.25rem;
		border-radius: 50%;
		font-size: var(--font-size-xs);
		font-weight: 700;
		border: 1px solid color-mix(in srgb, currentColor 55%, transparent);
	}

	.compound-soft {
		background: color-mix(in srgb, #d1453b 22%, transparent);
		color: #d1453b;
	}
	.compound-medium {
		background: color-mix(in srgb, #e0b400 22%, transparent);
		color: #9a7900;
	}
	.compound-hard {
		background: color-mix(in srgb, #8a8a8a 22%, transparent);
		color: var(--color-text);
	}
	.compound-intermediate {
		background: color-mix(in srgb, #3f9142 22%, transparent);
		color: #3f9142;
	}
	.compound-wet {
		background: color-mix(in srgb, #2f6fed 22%, transparent);
		color: #2f6fed;
	}
	.compound-unknown {
		background: transparent;
		color: var(--color-text-faint);
	}

	.tyre-age {
		color: var(--color-text-muted);
	}
</style>
