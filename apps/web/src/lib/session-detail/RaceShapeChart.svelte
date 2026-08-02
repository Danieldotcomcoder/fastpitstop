<script lang="ts">
	/** Race shape — gap-to-leader (default) or classification position, per
	 * selected driver, across the whole completed race. Fed by a single
	 * `/laps` fetch the parent page already owns (no WS replay needed: the
	 * race is over, so there is nothing left to pace against a delayed
	 * clock — the parent's `idleController`/`gateReveals=false` convention
	 * applies here too). Plain SVG, not Canvas: a few dozen points per driver
	 * over one race is nowhere near the "20 moving cars" threshold CLAUDE.md
	 * reserves Canvas/WebGL for. */
	import type { components } from '$lib/api/client';
	import { colorForDriverIndex } from './driver-palette';
	import {
		buildRaceShapeSeries,
		maxGapToLeader,
		maxLapNumber,
		maxPosition,
		type RaceShapeMetric
	} from './race-shape';

	type LapRow = components['schemas']['LapRowOut'];

	interface Props {
		laps: LapRow[];
		drivers: string[];
	}

	let { laps, drivers }: Props = $props();

	let metric = $state<RaceShapeMetric>('gap_to_leader');
	let series = $derived(buildRaceShapeSeries(laps, drivers));
	let lastLap = $derived(maxLapNumber(series));
	let gapMax = $derived(maxGapToLeader(series));
	let posMax = $derived(maxPosition(series));

	const WIDTH = 720;
	const HEIGHT = 260;
	const PAD = { top: 12, right: 16, bottom: 28, left: 44 };

	function xFor(lapNumber: number): number {
		if (lastLap <= 1) return PAD.left;
		return PAD.left + ((lapNumber - 1) / (lastLap - 1)) * (WIDTH - PAD.left - PAD.right);
	}

	function yFor(value: number): number {
		const innerHeight = HEIGHT - PAD.top - PAD.bottom;
		if (metric === 'gap_to_leader') {
			const max = gapMax && gapMax > 0 ? gapMax : 1;
			return PAD.top + (value / max) * innerHeight;
		}
		const max = posMax ?? 20;
		if (max <= 1) return PAD.top;
		return PAD.top + ((value - 1) / (max - 1)) * innerHeight;
	}

	function pathFor(points: { lapNumber: number; gapToLeaderS: number | null; position: number | null }[]): string {
		const usable = points.filter((p) =>
			metric === 'gap_to_leader' ? p.gapToLeaderS !== null : p.position !== null
		);
		if (usable.length === 0) return '';
		return usable
			.map((p, i) => {
				const value = metric === 'gap_to_leader' ? (p.gapToLeaderS as number) : (p.position as number);
				const x = xFor(p.lapNumber);
				const y = yFor(value);
				return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
			})
			.join(' ');
	}

	let hasData = $derived(series.some((s) => s.points.length > 0));
</script>

<div class="race-shape">
	<div class="toolbar">
		<div class="metric-toggle" role="group" aria-label="Chart metric">
			<button type="button" class:active={metric === 'gap_to_leader'} onclick={() => (metric = 'gap_to_leader')}>
				Gap to leader
			</button>
			<button type="button" class:active={metric === 'position'} onclick={() => (metric = 'position')}>
				Position
			</button>
		</div>
	</div>

	{#if !hasData}
		<p class="muted">No lap data yet for the selected drivers.</p>
	{:else}
		<div class="scroll">
			<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="Race shape over laps" class="chart">
				{#if metric === 'position'}
					<!-- Position: 1 at top reads as "the front of the race" -->
					<text x={PAD.left - 6} y={PAD.top + 4} class="axis-label" text-anchor="end">P1</text>
					<text x={PAD.left - 6} y={HEIGHT - PAD.bottom} class="axis-label" text-anchor="end">
						P{posMax ?? ''}
					</text>
				{:else}
					<text x={PAD.left - 6} y={PAD.top + 4} class="axis-label" text-anchor="end">0s</text>
					<text x={PAD.left - 6} y={HEIGHT - PAD.bottom} class="axis-label" text-anchor="end">
						{gapMax !== null ? `${gapMax.toFixed(0)}s` : ''}
					</text>
				{/if}
				<line
					x1={PAD.left}
					y1={PAD.top}
					x2={PAD.left}
					y2={HEIGHT - PAD.bottom}
					class="axis-line"
				/>
				<line
					x1={PAD.left}
					y1={HEIGHT - PAD.bottom}
					x2={WIDTH - PAD.right}
					y2={HEIGHT - PAD.bottom}
					class="axis-line"
				/>
				<text x={PAD.left} y={HEIGHT - 6} class="axis-label">Lap 1</text>
				<text x={WIDTH - PAD.right} y={HEIGHT - 6} class="axis-label" text-anchor="end">
					Lap {lastLap}
				</text>

				{#each series as s, i (s.driver)}
					<path d={pathFor(s.points)} fill="none" stroke={colorForDriverIndex(i)} stroke-width="2" />
				{/each}
			</svg>
		</div>
		<ul class="legend">
			{#each series as s, i (s.driver)}
				<li>
					<span class="swatch" style:background={colorForDriverIndex(i)}></span>
					<span class="code font-mono">{s.driver}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.race-shape {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.toolbar {
		display: flex;
		justify-content: flex-end;
	}

	.metric-toggle {
		display: inline-flex;
		border: 1px solid var(--hairline-strong);
		border-radius: var(--radius-sm);
		overflow: hidden;
	}

	.metric-toggle button {
		background: var(--surface-base);
		color: var(--text-muted);
		border: none;
		padding: var(--space-1) var(--space-3);
		font: inherit;
		font-size: var(--font-size-xs);
		cursor: pointer;
	}

	.metric-toggle button.active {
		background: var(--surface-overlay);
		color: var(--text-primary);
	}

	.scroll {
		overflow-x: auto;
	}

	.chart {
		width: 100%;
		min-width: 32rem;
		height: auto;
		display: block;
	}

	.axis-line {
		stroke: var(--hairline-strong);
		stroke-width: 1;
	}

	.axis-label {
		fill: var(--text-muted);
		font-size: 10px;
		font-family: var(--font-mono);
	}

	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
	}

	.legend li {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--text-secondary);
	}

	.swatch {
		width: 0.625rem;
		height: 0.625rem;
		border-radius: 50%;
		flex: none;
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
