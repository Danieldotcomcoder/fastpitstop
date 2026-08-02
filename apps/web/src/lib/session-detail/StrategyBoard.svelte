<script lang="ts">
	/** Stint bars + pit-stop markers per selected driver — derived from the
	 * same `/laps` fetch `RaceShapeChart` uses, one row per driver, one
	 * segment per stint coloured by compound (tyre colour IS the data here,
	 * unlike the categorical driver palette the gap chart needs). */
	import type { components } from '$lib/api/client';
	import { buildPitStops, buildStints, maxStintLap } from './strategy';
	import { tyreColorVar, tyreLabel } from './tyre-color';

	type LapRow = components['schemas']['LapRowOut'];

	interface Props {
		laps: LapRow[];
		drivers: string[];
	}

	let { laps, drivers }: Props = $props();

	let stints = $derived(buildStints(laps, drivers));
	let pitStops = $derived(buildPitStops(laps, drivers));
	let lastLap = $derived(maxStintLap(stints));

	function stintsFor(driver: string) {
		return stints.filter((s) => s.driver === driver);
	}

	function pitStopsFor(driver: string) {
		return pitStops.filter((p) => p.driver === driver);
	}

	function pct(lap: number): number {
		if (lastLap <= 1) return 0;
		return ((lap - 1) / (lastLap - 1)) * 100;
	}

	function widthPct(startLap: number, endLap: number): number {
		if (lastLap <= 1) return 100;
		return Math.max(((endLap - startLap + 1) / lastLap) * 100, 1.5);
	}

	let driversWithStints = $derived(drivers.filter((d) => stintsFor(d).length > 0));
</script>

<div class="strategy-board">
	{#if driversWithStints.length === 0}
		<p class="muted">No stint data yet for the selected drivers.</p>
	{:else}
		<div class="scroll">
			<div class="board" style:min-width="32rem">
				{#each driversWithStints as driver (driver)}
					<div class="row">
						<span class="code font-mono">{driver}</span>
						<div class="track">
							{#each stintsFor(driver) as stint (stint.stintNumber)}
								<div
									class="stint"
									style:left="{pct(stint.startLap)}%"
									style:width="{widthPct(stint.startLap, stint.endLap)}%"
									style:background="var({tyreColorVar(stint.compound)})"
									title="{tyreLabel(stint.compound)} · laps {stint.startLap}-{stint.endLap} ({stint.lapCount} laps)"
								></div>
							{/each}
							{#each pitStopsFor(driver) as stop (stop.lapNumber)}
								<div
									class="pit-marker"
									style:left="{pct(stop.lapNumber)}%"
									title="Pit stop, lap {stop.lapNumber}"
								></div>
							{/each}
						</div>
					</div>
				{/each}
				<div class="axis">
					<span>Lap 1</span>
					<span>Lap {lastLap}</span>
				</div>
			</div>
		</div>
		<ul class="legend">
			<li><span class="swatch" style:background="var(--tyre-soft)"></span>Soft</li>
			<li><span class="swatch" style:background="var(--tyre-medium)"></span>Medium</li>
			<li><span class="swatch" style:background="var(--tyre-hard)"></span>Hard</li>
			<li><span class="swatch" style:background="var(--tyre-inter)"></span>Intermediate</li>
			<li><span class="swatch" style:background="var(--tyre-wet)"></span>Wet</li>
			<li class="pit-legend"><span class="pit-marker-legend"></span>Pit stop</li>
		</ul>
	{/if}
</div>

<style>
	.strategy-board {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.scroll {
		overflow-x: auto;
	}

	.board {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.row {
		display: grid;
		grid-template-columns: 3rem 1fr;
		align-items: center;
		gap: var(--space-3);
	}

	.code {
		color: var(--text-secondary);
	}

	.track {
		position: relative;
		height: 1.25rem;
		background: var(--surface-overlay);
		border-radius: var(--radius-sm);
	}

	.stint {
		position: absolute;
		top: 0;
		bottom: 0;
		border-radius: var(--radius-sm);
		opacity: 0.85;
	}

	.pit-marker {
		position: absolute;
		top: -0.25rem;
		bottom: -0.25rem;
		width: 2px;
		background: var(--text-primary);
	}

	.pit-marker-legend {
		display: inline-block;
		width: 2px;
		height: 0.75rem;
		background: var(--text-primary);
	}

	.axis {
		display: flex;
		justify-content: space-between;
		padding-left: calc(3rem + var(--space-3));
		font-size: var(--font-size-xs);
		font-family: var(--font-mono);
		color: var(--text-muted);
	}

	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		font-size: var(--font-size-xs);
		color: var(--text-secondary);
	}

	.legend li {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
	}

	.swatch {
		width: 0.625rem;
		height: 0.625rem;
		border-radius: 2px;
		flex: none;
	}

	.pit-legend {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
