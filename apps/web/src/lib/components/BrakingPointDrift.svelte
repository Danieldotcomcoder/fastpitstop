<script lang="ts">
	import { driverName, type DriverNames } from '$lib/drivers';
	import { namesFromEvents } from '$lib/driver-events';
	/** Per-corner brake-application distance across a stint, for a selected
	 * driver — Phase 2.6's novel dataset (`f1_kernel.braking_points`), the
	 * telemetry answer to "tyre wear from images." SVG for the same reason
	 * as `DegradationCurves`: one corner's worth of points for one driver is
	 * a small, low-point-count chart where crisp vector lines beat Canvas.
	 *
	 * Two REST calls per (session, driver): `GET .../telemetry/{driver}`
	 * (raw per-lap braking-point rows) and `GET .../analysis?braking_drivers=`
	 * (the fitted `braking_drift` trend + its `reliability`). Both describe
	 * data that could reach past the user's playback clock, so — exactly
	 * like `DegradationCurves` — this gates on `$lib/kernel-views/
	 * RevealTracker`: a raw row only renders once ITS OWN lap has been
	 * completed on the visible event log (`isLapRevealed`, precise to the
	 * lap since braking rows carry `lap_number` directly), and a fitted
	 * drift line only once its whole stint is over (`isStintRevealed`). */
	import { api, type components } from '$lib/api/client';
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { RevealTracker } from '$lib/kernel-views';
	import { RaceStateReducer } from '$lib/track-map';
	import { formatSigned } from '$lib/estimate';
	import Estimate from './Estimate.svelte';
	import ConfidenceBadge from './ConfidenceBadge.svelte';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';

	type BrakingPoint = components['schemas']['BrakingPointRowOut'];
	type BrakingDrift = components['schemas']['BrakingDriftOut'];

	interface Props {
		names?: DriverNames;
		initialCorner?: number;
		apiKey: string;
		controller: DelaySyncController;
		/** When false, show every lap/fit immediately (completed-session view). */
		gateReveals?: boolean;
		/** Optional driver codes when the visible event log has no driver_list yet. */
		drivers?: string[];
	}

	let {
		apiKey,
		controller,
		gateReveals = true,
		drivers = [],
		names = {},
		initialCorner
	}: Props = $props();

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	let labels = $derived({ ...namesFromEvents(visible.events), ...names });
	$effect(() => {
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	const revealTracker = new RevealTracker();
	const raceReducer = new RaceStateReducer();

	let driverOptions = $derived.by(() => {
		raceReducer.update(visible.events);
		const fromStream = Array.from(raceReducer.raceState.cars.values())
			.filter((c) => c.driver)
			.sort((a, b) => Number(a.driver) - Number(b.driver));
		if (fromStream.length > 0)
			return drivers.length ? fromStream.filter((c) => drivers.includes(c.driver)) : fromStream;
		return drivers.map((driver) => ({
			driver,
			code: driver,
			name: null,
			team: null,
			compound: null,
			x: 0,
			y: 0,
			lastUpdateMs: 0
		}));
	});

	let selectedDriver = $state<string | null>(null);
	let effectiveDriver = $derived(
		driverOptions.some((d) => d.driver === selectedDriver)
			? selectedDriver
			: (driverOptions[0]?.driver ?? null)
	);

	function onDriverChange(event: Event): void {
		selectedDriver = (event.currentTarget as HTMLSelectElement).value;
	}

	let requestVersion = 0;
	let status = $state<'loading' | 'error' | 'ready'>('loading');
	let errorMessage = $state('');
	let allPoints = $state<BrakingPoint[]>([]);
	let allDrift = $state<BrakingDrift[]>([]);

	async function load(key: string, driver: string): Promise<void> {
		const version = ++requestVersion;
		status = 'loading';
		allPoints = [];
		allDrift = [];
		revealTracker.reset();
		try {
			const [pointsRes, analysisRes] = await Promise.all([
				api.GET('/sessions/{key}/telemetry/{driver}', { params: { path: { key, driver } } }),
				api.GET('/sessions/{key}/analysis', {
					params: { path: { key }, query: { braking_drivers: [driver] } }
				})
			]);
			if (version !== requestVersion) return;
			if (pointsRes.error) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			if (analysisRes.error) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			allPoints = pointsRes.data;
			allDrift = (analysisRes.data.braking_drift ?? []).filter((d) => d.driver === driver);
			status = 'ready';
		} catch {
			if (version !== requestVersion) return;
			status = 'error';
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	$effect(() => {
		const driver = effectiveDriver;
		if (driver) void load(apiKey, driver);
		return () => {
			requestVersion++;
		};
	});

	let revealedPoints = $derived.by(() => {
		if (!gateReveals) return allPoints;
		revealTracker.update(visible.events);
		const driver = effectiveDriver;
		if (!driver) return [];
		return allPoints.filter((p) => revealTracker.isLapRevealed(driver, p.lap_number));
	});

	let revealedDrift = $derived.by(() => {
		if (!gateReveals) return allDrift;
		revealTracker.update(visible.events);
		const driver = effectiveDriver;
		if (!driver) return [];
		return allDrift.filter((d) => revealTracker.isStintRevealed(d.driver, d.stint_number));
	});

	interface CornerOption {
		number: number;
		letter: string | null;
	}

	let cornerOptions = $derived.by((): CornerOption[] => {
		const result: CornerOption[] = [];
		for (const p of revealedPoints) {
			if (!result.some((c) => c.number === p.corner_number)) {
				result.push({ number: p.corner_number, letter: p.corner_letter });
			}
		}
		return result.sort((a, b) => a.number - b.number);
	});

	let selectedCorner = $state<number | null>(null);
	let effectiveCorner = $derived(
		cornerOptions.some((c) => c.number === selectedCorner)
			? selectedCorner
			: (cornerOptions.find((c) => c.number === initialCorner)?.number ??
					cornerOptions[0]?.number ??
					null)
	);

	function onCornerChange(event: Event): void {
		selectedCorner = Number((event.currentTarget as HTMLSelectElement).value);
	}

	function cornerLabel(c: CornerOption): string {
		return c.letter ? `Corner ${c.number}${c.letter}` : `Corner ${c.number}`;
	}

	let cornerPoints = $derived(
		revealedPoints
			.filter(
				(p) => p.corner_number === effectiveCorner && p.known && p.distance_before_corner_m !== null
			)
			.sort((a, b) => a.lap_number - b.lap_number)
	);
	let unknownCount = $derived(
		revealedPoints.filter((p) => p.corner_number === effectiveCorner && !p.known).length
	);
	let noDetectionCount = $derived(
		revealedPoints.filter(
			(p) => p.corner_number === effectiveCorner && p.known && p.distance_before_corner_m === null
		).length
	);

	let cornerDrift = $derived(
		revealedDrift.find((d) => d.corner_number === effectiveCorner) ?? null
	);

	/** Visible stint boundaries for the selected driver — decoration only
	 * (marks where a tyre change resets the trend), never used for gating. */
	let stintStartLaps = $derived.by((): number[] => {
		const driver = effectiveDriver;
		if (!driver) return [];
		const laps: number[] = [];
		for (const envelope of visible.events) {
			if (envelope.kind !== 'stint_started') continue;
			const payload = envelope.payload;
			if (payload.driver !== driver) continue;
			const startLap = payload.start_lap;
			if (typeof startLap === 'number') laps.push(startLap);
		}
		return laps;
	});

	const VB_W = 720;
	const VB_H = 260;
	const PAD = { top: 28, right: 24, bottom: 36, left: 55 };

	let lapDomain = $derived.by(() => {
		if (cornerPoints.length === 0) return { min: 0, max: 1 };
		const laps = cornerPoints.map((p) => p.lap_number);
		return { min: Math.min(...laps), max: Math.max(...laps) };
	});
	let distanceDomain = $derived.by(() => {
		const values = cornerPoints.map((p) => p.distance_before_corner_m as number);
		if (values.length === 0) return { min: 0, max: 1 };
		return { min: Math.min(...values), max: Math.max(...values) };
	});

	function xFor(lap: number): number {
		const { min, max } = lapDomain;
		const span = Math.max(max - min, 1);
		return PAD.left + ((lap - min) / span) * (VB_W - PAD.left - PAD.right);
	}
	function yFor(distance: number): number {
		const { min, max } = distanceDomain;
		const span = Math.max(max - min, 0.1);
		// braking EARLIER (larger distance before the corner) plots higher
		return PAD.top + (1 - (distance - min) / span) * (VB_H - PAD.top - PAD.bottom);
	}
	function linePath(): string {
		return cornerPoints
			.map(
				(p, i) =>
					`${i === 0 ? 'M' : 'L'} ${xFor(p.lap_number).toFixed(1)} ${yFor(p.distance_before_corner_m as number).toFixed(1)}`
			)
			.join(' ');
	}
</script>

<Panel
	title="The braking trace"
	subtitle="Higher points mean braking earlier before the corner. Each dot is one lap."
>
	{#if driverOptions.length === 0}
		<LoadingState label="Waiting for the driver list…" />
	{:else}
		<div class="selectors">
			<label>
				<span>Driver</span>
				<select onchange={onDriverChange}>
					{#each driverOptions as d (d.driver)}
						<option value={d.driver} selected={d.driver === effectiveDriver}
							>{labels[d.driver] ?? driverName(d.driver, null, d.code)}</option
						>
					{/each}
				</select>
			</label>
			{#if cornerOptions.length > 0}
				<label>
					<span>Corner</span>
					<select onchange={onCornerChange}>
						{#each cornerOptions as c (c.number)}
							<option value={c.number} selected={c.number === effectiveCorner}
								>{cornerLabel(c)}</option
							>
						{/each}
					</select>
				</label>
			{/if}
		</div>

		{#if status === 'loading'}
			<LoadingState label="Loading braking telemetry…" />
		{:else if status === 'error'}
			<p class="hint">{errorMessage}</p>
		{:else if allPoints.length === 0}
			<p class="hint">No braking telemetry for this driver in this session.</p>
		{:else if revealedPoints.length === 0}
			<p class="hint">
				Nothing revealed yet — this driver's first lap hasn't completed. Keep watching.
			</p>
		{:else if cornerPoints.length === 0}
			<p class="hint">
				No sustained braking event detected yet at this corner within the revealed laps
				{#if unknownCount > 0}({unknownCount} lap{unknownCount === 1 ? '' : 's'} had too little telemetry
					to judge){/if}.
			</p>
		{:else}
			<div class="plot-scroll">
				<svg
					viewBox="0 0 {VB_W} {VB_H}"
					class="chart"
					role="img"
					aria-label="Braking distance before corner across a stint"
				>
					<line
						x1={PAD.left}
						y1={VB_H - PAD.bottom}
						x2={VB_W - PAD.right}
						y2={VB_H - PAD.bottom}
						class="axis"
					/>
					<line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={VB_H - PAD.bottom} class="axis" />
					<text x={PAD.left} y={VB_H - 4} class="axis-label">lap →</text>
					<text x="4" y={PAD.top} class="axis-label">earlier</text>

					{#each stintStartLaps as startLap (startLap)}
						{#if startLap >= lapDomain.min && startLap <= lapDomain.max}
							<line
								x1={xFor(startLap)}
								y1={PAD.top}
								x2={xFor(startLap)}
								y2={VB_H - PAD.bottom}
								class="stint-boundary"
							/>
						{/if}
					{/each}

					<path
						d={linePath()}
						class="curve"
						stroke-dasharray={cornerDrift && cornerDrift.reliability !== 'good' ? '6 4' : undefined}
					/>
					{#each cornerPoints as p (p.lap_number)}
						<circle
							cx={xFor(p.lap_number)}
							cy={yFor(p.distance_before_corner_m as number)}
							r="2"
							class="point"
						/>
					{/each}
				</svg>
			</div>

			<div class="footnote">
				{#if unknownCount > 0}
					<span
						>{unknownCount} lap{unknownCount === 1 ? '' : 's'} had too little telemetry to judge.</span
					>
				{/if}
				{#if noDetectionCount > 0}
					<span
						>{noDetectionCount} lap{noDetectionCount === 1 ? '' : 's'} showed no sustained braking event.</span
					>
				{/if}
			</div>

			{#if cornerDrift}
				<ConfidenceBadge level={cornerDrift.reliability} />
				<Estimate
					label="Braking drift"
					value={cornerDrift.drift_m_per_lap}
					unit="m/lap"
					ciLow={cornerDrift.drift_ci_low_m_per_lap}
					ciHigh={cornerDrift.drift_ci_high_m_per_lap}
					sampleSize={cornerDrift.n_laps_used}
					method="robust regression"
					format={formatSigned}
				/>
			{:else}
				<p class="hint">
					No fitted drift trend revealed yet for this corner — its stint hasn't finished within your
					delay window.
				</p>
			{/if}
		{/if}
	{/if}
</Panel>

<style>
	.plot-scroll {
		overflow-x: auto;
	}
	.plot-scroll .chart {
		min-width: 540px;
	}
	.hint {
		color: var(--color-text-muted);
		font-size: var(--font-size-sm);
	}

	.selectors {
		display: flex;
		gap: var(--space-4);
		flex-wrap: wrap;
	}

	.selectors label {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
		flex: 1;
		min-width: 8rem;
	}

	select {
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
	}

	.chart {
		width: 100%;
		height: auto;
	}

	.axis {
		stroke: var(--color-border-strong);
		stroke-width: 1;
	}

	.axis-label {
		font-size: 12px;
		fill: var(--color-text-faint);
	}

	.stint-boundary {
		stroke: var(--color-border-strong);
		stroke-width: 1;
		stroke-dasharray: 3 3;
	}

	.curve {
		fill: none;
		stroke: var(--color-accent-strong);
		stroke-width: 2;
	}

	.point {
		fill: var(--color-accent-strong);
	}

	.footnote {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--color-text-faint);
	}
</style>
