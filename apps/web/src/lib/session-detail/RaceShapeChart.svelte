<script lang="ts">
	/** Race positions — the lap-by-lap position chart, plus gap-to-leader as
	 * a second view of the same data. Fed by a single `/laps` fetch the
	 * parent page already owns and gates through `RevealTracker`, so nothing
	 * ahead of the playback clock is ever drawn.
	 *
	 * Plain SVG, not Canvas: ~20 polylines of a few dozen points each is
	 * nowhere near the "20 moving cars at 60fps" threshold CLAUDE.md
	 * reserves Canvas/WebGL for, and vector text keeps the driver labels
	 * crisp.
	 *
	 * The design is deliberately the one an F1 viewer already knows how to
	 * read, rather than a generic multi-series line chart:
	 *
	 * - **Team colour, teammate dashed.** 20 drivers cannot be told apart by
	 *   20 arbitrary hues (a 10-colour palette repeats), but every fan
	 *   already reads two papaya cars as McLaren. `teammateDashOrder` gives
	 *   the second car a dashed stroke, the same convention broadcast
	 *   graphics use.
	 * - **Labels on the lines.** Driver codes sit at the right-hand edge in
	 *   running order, so the chart answers "who is that line" without a
	 *   hover and without a 20-chip legend block.
	 * - **Pit stops marked.** A position line dropping five places is noise
	 *   until you can see it was a pit stop.
	 * - **Safety car / VSC shaded.** The same for a lap where the whole
	 *   field bunches up.
	 * - **A hover crosshair** reads out the actual running order on that
	 *   lap — the thing you actually want when a chart has 20 lines. */
	import { driverName, type DriverNames } from '$lib/drivers';
	import type { components } from '$lib/api/client';
	import { colorForTeam } from '$lib/teams';
	import { tyreColorVar } from './tyre-color';
	import {
		buildRaceShapeSeries,
		latestPosition,
		maxGapToLeader,
		maxLapNumber,
		maxPosition,
		pitMarkers,
		teammateDashOrder,
		trackStatusBands,
		type DriverRaceShape,
		type RaceShapeMetric,
		type RaceShapePoint
	} from './race-shape';

	type LapRow = components['schemas']['LapRowOut'];

	interface Props {
		names?: DriverNames;
		laps: LapRow[];
		drivers: string[];
		/** Driver number -> team name, from the timing feed's own
		 * `driver_list_updated`. `/laps` carries no team at all, which is why
		 * this arrives as a prop rather than being derived here. */
		teams?: Record<string, string | null>;
		/** Driver number -> three-letter code, for the on-chart labels. */
		codes?: Record<string, string | null>;
	}

	let { laps, drivers, names = {}, teams = {}, codes = {} }: Props = $props();

	const METRICS: { id: RaceShapeMetric; label: string; hint: string }[] = [
		{
			id: 'position',
			label: 'Position',
			hint: 'P1 at the top. Crossing lines are overtakes; a sudden drop is usually a pit stop.'
		},
		{
			id: 'gap_to_leader',
			label: 'Gap to leader',
			hint: 'Distance behind P1 in seconds. Converging lines mean the pack is closing up.'
		}
	];

	let metric = $state<RaceShapeMetric>('position');
	let focused = $state<string | null>(null);
	let hoverLap = $state<number | null>(null);

	let series = $derived(buildRaceShapeSeries(laps, drivers));
	let lastLap = $derived(maxLapNumber(series));
	let gapMax = $derived(maxGapToLeader(series));
	let posMax = $derived(maxPosition(series) ?? 20);
	let bands = $derived(trackStatusBands(laps));
	let stops = $derived(pitMarkers(series));
	let hint = $derived(METRICS.find((m) => m.id === metric)?.hint ?? '');

	/** Running order at the latest revealed lap — the order the right-hand
	 * labels and the hover readout both use. */
	let ordered = $derived(
		series
			.map((shape) => ({ shape, position: latestPosition(shape) }))
			.sort((a, b) => (a.position ?? 99) - (b.position ?? 99))
	);
	let dashed = $derived(
		teammateDashOrder(
			ordered.map((o) => o.shape.driver),
			(driver) => teams[driver] ?? null
		)
	);

	function label(driver: string): string {
		return codes[driver] ?? driverName(driver).slice(0, 3).toUpperCase();
	}
	function fullLabel(driver: string): string {
		return names[driver] ?? driverName(driver);
	}
	function strokeFor(driver: string): string {
		return colorForTeam(teams[driver] ?? null);
	}

	const WIDTH = 860;
	const HEIGHT = 420;
	// Right pad carries the driver-code label column.
	const PAD = { top: 18, right: 54, bottom: 34, left: 46 };
	const INNER_W = WIDTH - PAD.left - PAD.right;
	const INNER_H = HEIGHT - PAD.top - PAD.bottom;

	function xFor(lapNumber: number): number {
		if (lastLap <= 1) return PAD.left;
		return PAD.left + ((lapNumber - 1) / (lastLap - 1)) * INNER_W;
	}

	function yFor(value: number): number {
		if (metric === 'gap_to_leader') {
			const max = gapMax && gapMax > 0 ? gapMax : 1;
			return PAD.top + (value / max) * INNER_H;
		}
		if (posMax <= 1) return PAD.top;
		return PAD.top + ((value - 1) / (posMax - 1)) * INNER_H;
	}

	function valueOf(point: RaceShapePoint): number | null {
		return metric === 'gap_to_leader' ? point.gapToLeaderS : point.position;
	}

	function pathFor(points: RaceShapePoint[]): string {
		let out = '';
		let pen = false;
		for (const point of points) {
			const value = valueOf(point);
			if (value === null) {
				// A gap the feed never reported must break the line, not
				// interpolate across it into a shape that never happened.
				pen = false;
				continue;
			}
			out += `${pen ? 'L' : 'M'}${xFor(point.lapNumber).toFixed(1)},${yFor(value).toFixed(1)} `;
			pen = true;
		}
		return out.trim();
	}

	function pointAt(shape: DriverRaceShape, lapNumber: number): RaceShapePoint | null {
		return shape.points.find((p) => p.lapNumber === lapNumber) ?? null;
	}

	/** Label y positions, nudged apart so two drivers on the same lap
	 * position (or an identical gap) don't render their codes on top of
	 * each other. */
	let labelRows = $derived.by(() => {
		const rows: { driver: string; y: number }[] = [];
		let previous = -Infinity;
		for (const { shape } of ordered) {
			const last = [...shape.points].reverse().find((p) => valueOf(p) !== null);
			if (last === undefined) continue;
			const wanted = yFor(valueOf(last) as number);
			const y = Math.max(wanted, previous + 11);
			rows.push({ driver: shape.driver, y });
			previous = y;
		}
		return rows;
	});

	let hoverOrder = $derived.by(() => {
		if (hoverLap === null) return [];
		return series
			.map((shape) => ({ driver: shape.driver, point: pointAt(shape, hoverLap as number) }))
			.filter((entry) => entry.point !== null && entry.point.position !== null)
			.sort((a, b) => (a.point!.position as number) - (b.point!.position as number));
	});

	function onMove(event: MouseEvent): void {
		const svg = event.currentTarget as SVGSVGElement;
		const rect = svg.getBoundingClientRect();
		const ratio = (event.clientX - rect.left) / rect.width;
		const x = ratio * WIDTH;
		if (lastLap <= 1) {
			hoverLap = lastLap;
			return;
		}
		const lap = Math.round(1 + ((x - PAD.left) / INNER_W) * (lastLap - 1));
		hoverLap = Math.min(lastLap, Math.max(1, lap));
	}

	let hasData = $derived(series.some((s) => s.points.length > 0));
	let gridValues = $derived(
		metric === 'position'
			? [1, 5, 10, 15, 20].filter((p) => p <= posMax)
			: [0, 0.25, 0.5, 0.75, 1].map((f) => f * (gapMax ?? 0))
	);
</script>

<div class="race-shape">
	<div class="toolbar">
		<p class="chart-guide">{hint}</p>
		<label class="metric-picker">
			<span class="visually-hidden">Chart type</span>
			<select bind:value={metric} aria-label="Chart type">
				{#each METRICS as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
			</select>
		</label>
	</div>

	{#if !hasData}
		<p class="muted">No lap data yet for the selected drivers.</p>
	{:else}
		<div class="scroll">
			<svg
				viewBox="0 0 {WIDTH} {HEIGHT}"
				role="img"
				aria-label="Race {metric === 'position' ? 'positions' : 'gaps'} by lap"
				class="chart"
				onmousemove={onMove}
				onmouseleave={() => (hoverLap = null)}
			>
				<!-- Neutralised laps, behind everything: context first. -->
				{#each bands as band (band.status + band.fromLap)}
					<rect
						x={xFor(band.fromLap)}
						y={PAD.top}
						width={Math.max(3, xFor(band.toLap) - xFor(band.fromLap))}
						height={INNER_H}
						class="band band-{band.status}"
					/>
					<text x={xFor(band.fromLap) + 3} y={PAD.top + 10} class="band-label"
						>{band.status.toUpperCase()}</text
					>
				{/each}

				{#each gridValues as value (value)}
					<line
						x1={PAD.left}
						x2={WIDTH - PAD.right}
						y1={yFor(value)}
						y2={yFor(value)}
						class="grid-line"
					/>
					<text x={PAD.left - 8} y={yFor(value) + 3.5} class="axis-label" text-anchor="end">
						{metric === 'position' ? `P${value}` : `${value.toFixed(0)}s`}
					</text>
				{/each}

				<line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={HEIGHT - PAD.bottom} class="axis-line" />
				<line
					x1={PAD.left}
					y1={HEIGHT - PAD.bottom}
					x2={WIDTH - PAD.right}
					y2={HEIGHT - PAD.bottom}
					class="axis-line"
				/>
				<text x={PAD.left} y={HEIGHT - 8} class="axis-label">Lap 1</text>
				<text x={WIDTH - PAD.right} y={HEIGHT - 8} class="axis-label" text-anchor="end"
					>Lap {lastLap}</text
				>

				{#if hoverLap !== null}
					<line
						x1={xFor(hoverLap)}
						x2={xFor(hoverLap)}
						y1={PAD.top}
						y2={HEIGHT - PAD.bottom}
						class="crosshair"
					/>
				{/if}

				{#each ordered as { shape } (shape.driver)}
					{@const dim = focused !== null && focused !== shape.driver}
					<path
						d={pathFor(shape.points)}
						fill="none"
						stroke={strokeFor(shape.driver)}
						stroke-width={focused === shape.driver ? 3.2 : 1.8}
						stroke-dasharray={dashed[shape.driver] ? '7 4' : undefined}
						stroke-linejoin="round"
						opacity={dim ? 0.12 : 0.95}
						><title>{fullLabel(shape.driver)}</title></path
					>
				{/each}

				<!-- Pit stops last so they sit above every line. -->
				{#each stops as stop (stop.driver + stop.lapNumber)}
					{@const point = pointAt(
						series.find((s) => s.driver === stop.driver) as DriverRaceShape,
						stop.lapNumber
					)}
					{#if point && valueOf(point) !== null}
						{@const dim = focused !== null && focused !== stop.driver}
						<rect
							x={xFor(stop.lapNumber) - 2.6}
							y={yFor(valueOf(point) as number) - 2.6}
							width="5.2"
							height="5.2"
							transform="rotate(45 {xFor(stop.lapNumber)} {yFor(valueOf(point) as number)})"
							class="pit-marker"
							style:fill={point.compound
								? `var(${tyreColorVar(point.compound)})`
								: 'var(--text-primary)'}
							opacity={dim ? 0.12 : 1}
						>
							<title>{fullLabel(stop.driver)} pitted on lap {stop.lapNumber}</title>
						</rect>
					{/if}
				{/each}

				{#each labelRows as row (row.driver)}
					{@const dim = focused !== null && focused !== row.driver}
					<text
						x={WIDTH - PAD.right + 6}
						y={row.y + 3}
						class="driver-label"
						fill={strokeFor(row.driver)}
						opacity={dim ? 0.2 : 1}>{label(row.driver)}</text
					>
				{/each}
			</svg>
		</div>

		{#if hoverLap !== null && hoverOrder.length}
			<div class="hover-readout" aria-live="off">
				<span class="hover-lap">LAP {hoverLap}</span>
				<ol>
					{#each hoverOrder.slice(0, 10) as entry (entry.driver)}
						<li>
							<span class="pos">{entry.point?.position}</span>
							<span class="chip" style:background={strokeFor(entry.driver)}></span>
							<span class="who">{label(entry.driver)}</span>
							{#if entry.point?.gapToLeaderS !== null && entry.point?.gapToLeaderS !== undefined}
								<span class="gap tabular"
									>{entry.point.gapToLeaderS === 0
										? 'LEADER'
										: `+${entry.point.gapToLeaderS.toFixed(1)}`}</span
								>
							{/if}
						</li>
					{/each}
				</ol>
			</div>
		{/if}

		<div class="chart-key">
			<span><span class="key-diamond"></span> Pit stop</span>
			<span><span class="key-dash"></span> Second car of the team</span>
			<span><span class="key-band"></span> Safety car / VSC</span>
		</div>

		<ul class="legend">
			{#each ordered as { shape, position } (shape.driver)}
				<li>
					<button
						type="button"
						class:focused={focused === shape.driver}
						aria-pressed={focused === shape.driver}
						onclick={() => (focused = focused === shape.driver ? null : shape.driver)}
					>
						<span class="swatch" style:background={strokeFor(shape.driver)}></span>
						<span class="pos tabular">{position ?? '—'}</span>
						<span class="code">{fullLabel(shape.driver)}</span>
					</button>
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
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
		flex-wrap: wrap;
	}

	.chart-guide {
		margin: 0;
		color: var(--text-secondary);
		font-size: var(--font-size-sm);
		max-width: 62ch;
	}

	.metric-picker select {
		padding: 0.55rem 0.7rem;
		border-radius: var(--radius-sm);
		border: 1px solid var(--hairline-strong);
		background: var(--surface-base);
		color: var(--text-primary);
		font: inherit;
		font-size: var(--font-size-sm);
		min-height: 40px;
	}

	.scroll {
		overflow-x: auto;
	}

	.chart {
		width: 100%;
		min-width: 40rem;
		height: auto;
		display: block;
	}

	.grid-line {
		stroke: var(--hairline);
		stroke-dasharray: 3 6;
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

	.band-sc {
		fill: color-mix(in srgb, var(--timing-yellow) 16%, transparent);
	}
	.band-vsc {
		fill: color-mix(in srgb, var(--timing-yellow) 9%, transparent);
	}
	.band-red {
		fill: color-mix(in srgb, var(--flag-red) 16%, transparent);
	}

	.band-label {
		fill: var(--timing-yellow);
		font-size: 8px;
		font-family: var(--font-mono);
		letter-spacing: 0.06em;
	}

	.crosshair {
		stroke: var(--text-secondary);
		stroke-width: 1;
		stroke-dasharray: 2 4;
	}

	.driver-label {
		font: 500 10px var(--font-mono);
		letter-spacing: 0.03em;
	}

	.pit-marker {
		stroke: var(--surface-base);
		stroke-width: 1;
	}

	.hover-readout {
		display: flex;
		gap: var(--space-3);
		align-items: baseline;
		flex-wrap: wrap;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-sm);
		background: var(--surface-base);
	}

	.hover-lap {
		font: 0.7rem var(--font-mono);
		letter-spacing: 0.1em;
		color: var(--text-muted);
	}

	.hover-readout ol {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-3);
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.hover-readout li {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: var(--font-size-xs);
	}

	.hover-readout .pos {
		color: var(--text-muted);
		font-family: var(--font-mono);
		min-width: 1.2em;
		text-align: right;
	}

	.hover-readout .chip {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 1px;
	}

	.hover-readout .gap {
		color: var(--text-muted);
	}

	.chart-key {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-4);
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.chart-key span {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}

	.key-diamond {
		width: 0.5rem;
		height: 0.5rem;
		background: var(--text-secondary);
		transform: rotate(45deg);
	}

	.key-dash {
		width: 1.1rem;
		border-top: 2px dashed var(--text-secondary);
	}

	.key-band {
		width: 0.9rem;
		height: 0.7rem;
		background: color-mix(in srgb, var(--timing-yellow) 22%, transparent);
	}

	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: var(--space-1);
	}

	.legend button {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		background: transparent;
		color: var(--text-secondary);
		padding: 0.4rem 0.5rem;
		font: inherit;
		font-size: var(--font-size-xs);
		cursor: pointer;
		text-align: left;
		min-height: 34px;
	}

	.legend button:hover,
	.legend button.focused {
		color: var(--text-primary);
		background: var(--surface-overlay);
		border-color: var(--hairline-strong);
	}

	.legend .pos {
		font-family: var(--font-mono);
		color: var(--text-muted);
		min-width: 1.4em;
		text-align: right;
	}

	.legend .code {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.swatch {
		width: 0.5rem;
		height: 0.9rem;
		border-radius: 1px;
		flex: none;
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}

	@media (max-width: 700px) {
		.legend {
			grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr));
		}
	}
</style>
