<script lang="ts">
	/** Braking — a corner-by-corner braking map for ONE driver.
	 *
	 * Two deliberate choices, both learned from what this view used to do:
	 *
	 * - **One driver, not the whole field.** It previously asked
	 *   `/analysis?braking_drivers=` for every driver on the timing feed.
	 *   That is a telemetry load per driver: minutes of server work, so the
	 *   panel sat on "Checking the available braking telemetry…" forever and
	 *   effectively never rendered. One driver answers in a few seconds.
	 * - **One row per corner, not per (stint, corner) fit.** The API returns
	 *   a fit per stint per corner — 45-ish rows for a three-stop race, most
	 *   of them `thin`. `buildCornerBraking` rolls those into the question a
	 *   fan actually asks: where does this driver brake, and is that point
	 *   moving? The strongest fit per corner carries its own reliability.
	 *
	 * Spoiler-safety is unchanged and still comes from `RevealTracker`: a
	 * per-lap braking row renders only once that lap has completed on the
	 * visible event log, a fitted trend only once its stint has ended. */
	import { api, type components } from '$lib/api/client';
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { RevealTracker } from '$lib/kernel-views';
	import { driverName, type DriverNames } from '$lib/drivers';
	import { colorForTeam } from '$lib/teams';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import {
		buildCornerBraking,
		driftDirection,
		DRIFT_LABEL,
		headlineCorner
	} from './braking';

	type BrakingPoint = components['schemas']['BrakingPointRowOut'];
	type BrakingDrift = components['schemas']['BrakingDriftOut'];

	interface Props {
		apiKey: string;
		controller: DelaySyncController;
		/** The picker's options. Must be a STABLE list (sorted, not running
		 * order): the driver drives a multi-second telemetry fetch, so a
		 * list that reorders every time someone overtakes would restart it
		 * constantly. */
		drivers: string[];
		names: DriverNames;
		teams?: Record<string, string | null>;
		/** Who to open on — the race leader at the moment this first
		 * renders. Latched once (see below) rather than followed, for the
		 * same reason. */
		defaultDriver?: string | null;
	}

	let { apiKey, controller, drivers, names, teams = {}, defaultDriver = null }: Props = $props();

	let selected = $state<string | null>(null);
	// Latch the default exactly once, the first time there is a field at
	// all. Following `defaultDriver` live would re-fetch telemetry on every
	// lead change; leaving it unset would open on an arbitrary driver
	// number instead of whoever is actually winning.
	$effect(() => {
		if (selected === null && (defaultDriver ?? drivers[0])) {
			selected = defaultDriver ?? (drivers[0] as string);
		}
	});
	let points = $state<BrakingPoint[]>([]);
	let drifts = $state<BrakingDrift[]>([]);
	let status = $state<'idle' | 'loading' | 'ready' | 'error'>('idle');
	let openCorner = $state<number | null>(null);

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	const reveal = new RevealTracker();
	$effect(() => {
		reveal.reset();
		return controller.visibleState.subscribe((v) => (visible = v));
	});

	/** The driver actually shown — always the latched/selected one. */
	let driver = $derived(selected);

	let requestVersion = 0;
	$effect(() => {
		const target = driver;
		if (!target) {
			status = 'idle';
			return;
		}
		const version = ++requestVersion;
		status = 'loading';
		points = [];
		drifts = [];
		openCorner = null;
		void Promise.all([
			api.GET('/sessions/{key}/telemetry/{driver}', {
				params: { path: { key: apiKey, driver: target } }
			}),
			api.GET('/sessions/{key}/analysis', {
				params: { path: { key: apiKey }, query: { braking_drivers: [target] } }
			})
		])
			.then(([telemetry, analysis]) => {
				if (version !== requestVersion) return;
				points = telemetry.data ?? [];
				drifts = (analysis.data?.braking_drift ?? []).filter((d) => d.driver === target);
				status = telemetry.error ? 'error' : 'ready';
			})
			.catch(() => {
				if (version !== requestVersion) return;
				status = 'error';
			});
		return () => {
			requestVersion++;
		};
	});

	let revealedPoints = $derived.by(() => {
		reveal.update(visible.events);
		return points.filter((p) => reveal.isLapRevealed(p.driver, p.lap_number));
	});
	let revealedDrifts = $derived.by(() => {
		reveal.update(visible.events);
		return drifts.filter((d) => reveal.isStintRevealed(d.driver, d.stint_number));
	});
	let corners = $derived(buildCornerBraking(revealedPoints, revealedDrifts));
	let headline = $derived(headlineCorner(corners));
	let widest = $derived(Math.max(1, ...corners.map((c) => c.maxDistanceM)));

	function label(d: string): string {
		return names[d] ?? driverName(d);
	}
	function accent(d: string | null): string {
		return colorForTeam(d ? (teams[d] ?? null) : null);
	}

	/** Per-lap points for the expanded corner, oldest first — the evidence
	 * behind that corner's own row. */
	function lapSeries(cornerNumber: number): { lap: number; distance: number }[] {
		return revealedPoints
			.filter((p) => p.corner_number === cornerNumber && p.distance_before_corner_m !== null)
			.map((p) => ({ lap: p.lap_number, distance: p.distance_before_corner_m as number }))
			.sort((a, b) => a.lap - b.lap);
	}

	const SPARK_W = 320;
	const SPARK_H = 90;
</script>

<div class="braking">
	<div class="picker-row">
		<label class="driver-picker">
			<span>Driver</span>
			<select
				value={driver ?? ''}
				onchange={(e) => (selected = (e.currentTarget as HTMLSelectElement).value)}
				disabled={drivers.length === 0}
			>
				{#each drivers as d (d)}<option value={d}>{label(d)}</option>{/each}
			</select>
		</label>
		{#if driver}
			<span class="driver-flag" style:background={accent(driver)}></span>
		{/if}
	</div>

	{#if drivers.length === 0}
		<p class="availability">Waiting for the field to reach your playback clock.</p>
	{:else if status === 'loading'}
		<LoadingState label="Reading the brake traces…" />
	{:else if status === 'error'}
		<p class="availability">
			Braking telemetry could not be loaded for this driver. Timing and tyre performance remain
			available.
		</p>
	{:else if corners.length === 0}
		<div class="availability">
			<span class="data-label">DATA AVAILABILITY</span>
			<h3>No corner-level braking data yet.</h3>
			<p>
				This view needs brake telemetry and a measured circuit layout, and only shows laps your
				playback clock has already reached. Corners appear as the race runs.
			</p>
		</div>
	{:else}
		{#if headline}
			<p class="headline">
				<strong>Turn {headline.cornerNumber}</strong> is where
				{label(driver as string)} is moving most —
				<strong
					>{Math.abs(headline.drift?.drift_m_per_lap ?? 0).toFixed(1)} m
					{driftDirection(headline.drift) === 'earlier' ? 'earlier' : 'later'} per lap</strong
				> as the stint runs on.
			</p>
		{:else}
			<p class="headline subtle">
				Braking points measured at {corners.length} corners. No corner shows a trend strong enough
				to call a direction yet.
			</p>
		{/if}

		<ul class="corner-map">
			{#each corners as corner (corner.cornerNumber)}
				{@const direction = driftDirection(corner.drift)}
				<li class:open={openCorner === corner.cornerNumber}>
					<button
						type="button"
						onclick={() =>
							(openCorner = openCorner === corner.cornerNumber ? null : corner.cornerNumber)}
						aria-expanded={openCorner === corner.cornerNumber}
					>
						<span class="corner-id"
							>T{corner.cornerNumber}{corner.cornerLetter ?? ''}</span
						>

						<span class="bar-track">
							<!-- Spread first (min..max), median marker on top: the
							     spread is the consistency story, the median the headline. -->
							<span
								class="bar-spread"
								style:left="{(corner.minDistanceM / widest) * 100}%"
								style:width="{((corner.maxDistanceM - corner.minDistanceM) / widest) * 100}%"
							></span>
							<span
								class="bar-median"
								style:left="{(corner.medianDistanceM / widest) * 100}%"
								style:background={accent(driver)}
							></span>
						</span>

						<span class="distance tabular">{corner.medianDistanceM.toFixed(0)}<small>m</small></span>

						<span class="trend trend-{direction}">
							{#if direction === 'earlier'}▲{:else if direction === 'later'}▼{:else if direction === 'steady'}—{:else}·{/if}
							<span class="trend-text">
								{#if direction === 'earlier' || direction === 'later'}
									{Math.abs(corner.drift?.drift_m_per_lap ?? 0).toFixed(1)} m/lap
								{:else}
									{DRIFT_LABEL[direction]}
								{/if}
							</span>
						</span>

						{#if corner.drift}<ConfidenceBadge level={corner.drift.reliability} />{/if}
					</button>

					{#if openCorner === corner.cornerNumber}
						{@const series = lapSeries(corner.cornerNumber)}
						<div class="corner-detail">
							<p class="detail-note">
								{corner.lapsDetected} revealed lap{corner.lapsDetected === 1 ? '' : 's'} with a
								measured brake application{corner.lapsWithoutBraking > 0
									? `, ${corner.lapsWithoutBraking} taken without braking`
									: ''}. Higher on the chart means braking earlier.
							</p>
							{#if series.length > 1}
								{@const lo = Math.min(...series.map((s) => s.distance))}
								{@const hi = Math.max(...series.map((s) => s.distance))}
								{@const firstLap = series[0]?.lap ?? 1}
								{@const lastLap = series[series.length - 1]?.lap ?? firstLap + 1}
								{@const span = Math.max(1, lastLap - firstLap)}
								{@const range = Math.max(1, hi - lo)}
								<svg
									viewBox="0 0 {SPARK_W} {SPARK_H}"
									class="spark"
									role="img"
									aria-label="Braking distance by lap at turn {corner.cornerNumber}"
								>
									<polyline
										fill="none"
										stroke={accent(driver)}
										stroke-width="1.6"
										points={series
											.map(
												(s) =>
													`${(((s.lap - firstLap) / span) * (SPARK_W - 8) + 4).toFixed(1)},${(
														SPARK_H -
														8 -
														((s.distance - lo) / range) * (SPARK_H - 16)
													).toFixed(1)}`
											)
											.join(' ')}
									/>
									{#each series as s (s.lap)}
										<circle
											cx={((s.lap - firstLap) / span) * (SPARK_W - 8) + 4}
											cy={SPARK_H - 8 - ((s.distance - lo) / range) * (SPARK_H - 16)}
											r="1.8"
											fill={accent(driver)}
										/>
									{/each}
								</svg>
								<div class="spark-axis tabular">
									<span>Lap {firstLap}</span>
									<span>{lo.toFixed(0)}–{hi.toFixed(0)} m</span>
									<span>Lap {lastLap}</span>
								</div>
							{:else}
								<p class="detail-note">Not enough revealed laps to draw a trace yet.</p>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>

		<p class="footnote">
			Distance is how far before the corner the brakes are applied and held. Braking earlier
			through a stint is the classic sign of a tyre or brake fading. Measured from the car's own
			brake channel — no camera, no guesswork.
		</p>
	{/if}
</div>

<style>
	.braking {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.picker-row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.driver-picker {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.driver-picker select {
		padding: 0.55rem 0.7rem;
		border-radius: var(--radius-sm);
		border: 1px solid var(--hairline-strong);
		background: var(--surface-base);
		color: var(--text-primary);
		font: inherit;
		font-size: var(--font-size-sm);
		min-height: 40px;
	}

	.driver-flag {
		width: 0.35rem;
		height: 1.6rem;
		border-radius: 1px;
	}

	.headline {
		margin: 0;
		color: var(--text-primary);
		font-size: var(--font-size-sm);
		line-height: 1.6;
	}

	.headline.subtle {
		color: var(--text-secondary);
	}

	.corner-map {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--hairline);
		border-radius: var(--radius-md);
		overflow: hidden;
	}

	.corner-map li + li {
		border-top: 1px solid var(--hairline);
	}

	.corner-map li.open {
		background: var(--surface-base);
	}

	.corner-map button {
		display: grid;
		grid-template-columns: 3rem minmax(4rem, 1fr) 4.2rem 8rem auto;
		align-items: center;
		gap: var(--space-3);
		width: 100%;
		padding: 0.7rem 0.9rem;
		border: 0;
		background: transparent;
		color: var(--text-primary);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.corner-map button:hover {
		background: var(--surface-overlay);
	}

	.corner-id {
		font: 500 0.85rem var(--font-mono);
		color: var(--text-secondary);
	}

	.bar-track {
		position: relative;
		height: 0.55rem;
		background: var(--surface-overlay);
		border-radius: 2px;
	}

	.bar-spread {
		position: absolute;
		top: 0;
		bottom: 0;
		background: var(--hairline-strong);
		border-radius: 2px;
		min-width: 2px;
	}

	.bar-median {
		position: absolute;
		top: -2px;
		bottom: -2px;
		width: 3px;
		border-radius: 1px;
	}

	.distance {
		text-align: right;
		font-size: 0.95rem;
	}

	.distance small {
		color: var(--text-muted);
		font-size: 0.7rem;
		margin-left: 1px;
	}

	.trend {
		display: inline-flex;
		align-items: baseline;
		gap: 0.35rem;
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.trend-text {
		font-family: var(--font-mono);
	}

	.trend-earlier {
		color: var(--timing-yellow);
	}

	.trend-later {
		color: var(--timing-green);
	}

	.corner-detail {
		padding: 0 0.9rem 0.9rem 3.9rem;
	}

	.detail-note {
		margin: 0 0 var(--space-2);
		color: var(--text-secondary);
		font-size: var(--font-size-xs);
		line-height: 1.6;
	}

	.spark {
		width: 100%;
		max-width: 26rem;
		height: auto;
		display: block;
	}

	.spark-axis {
		display: flex;
		justify-content: space-between;
		max-width: 26rem;
		font-size: 0.65rem;
		color: var(--text-muted);
	}

	.footnote {
		margin: 0;
		color: var(--text-muted);
		font-size: var(--font-size-xs);
		line-height: 1.6;
		max-width: 70ch;
	}

	.availability {
		padding: 2rem 1.5rem;
		background: var(--surface-base);
		border: 1px dashed var(--hairline-strong);
		border-radius: var(--radius-md);
		color: var(--text-secondary);
		font-size: var(--font-size-sm);
		line-height: 1.6;
	}

	.availability h3 {
		font-size: 1.3rem;
		color: var(--text-primary);
		margin: 0.8rem 0;
	}

	.availability p {
		margin: 0;
		max-width: 60ch;
	}

	.data-label {
		font: 0.65rem var(--font-mono);
		letter-spacing: 0.1em;
	}

	@media (max-width: 720px) {
		.corner-map button {
			grid-template-columns: 2.4rem 1fr 3.6rem;
			grid-template-areas:
				'id bar dist'
				'id trend trend';
			row-gap: 0.35rem;
		}
		.corner-id {
			grid-area: id;
		}
		.bar-track {
			grid-area: bar;
		}
		.distance {
			grid-area: dist;
		}
		.trend {
			grid-area: trend;
		}
		.corner-detail {
			padding-left: 0.9rem;
		}
	}
</style>
