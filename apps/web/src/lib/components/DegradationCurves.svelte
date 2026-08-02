<script lang="ts">
	/** Fuel-corrected pace vs tyre age per stint, with a crossover marked —
	 * `f1_kernel.fit_degradation`'s output (`GET /sessions/{key}/degradation`)
	 * rendered as an SVG curve chart. SVG rather than Canvas: at most a
	 * couple of fitted lines are ever on screen at once (two curves plus a
	 * CI band each), so crisp vector strokes and text are worth more here
	 * than Canvas's raw-throughput advantage — the opposite tradeoff from
	 * the gap-stream graph's thousands of points.
	 *
	 * `/degradation` describes the WHOLE session up front, which would leak
	 * future stints if rendered the moment it arrives — this is the one
	 * REST-fetched view (with `BrakingPointDrift`) that needs its own
	 * spoiler gate: `$lib/kernel-views/RevealTracker` folds
	 * `controller.visibleState.events` (already delay-gated) into "which
	 * stints have provably finished" and this view only ever renders a fit
	 * once that says yes. See `$lib/kernel-views/reveal.ts`'s module doc for
	 * the full reasoning. */
	import { api, type components } from '$lib/api/client';
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import {
		degradationCrossover,
		degradationCurve,
		RevealTracker,
		type CurveSeries
	} from '$lib/kernel-views';
	import { formatSigned } from '$lib/estimate';
	import ConfidenceBadge from './ConfidenceBadge.svelte';
	import Estimate from './Estimate.svelte';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';

	type DegradationFit = components['schemas']['DegradationFitOut'];

	interface Props {
		apiKey: string;
		controller: DelaySyncController;
		/** When false, show every fit immediately (completed-session retrospective).
		 * Live/replay paths keep the default Delay Sync + RevealTracker gate. */
		gateReveals?: boolean;
	}

	let { apiKey, controller, gateReveals = true }: Props = $props();

	let status = $state<'loading' | 'error' | 'ready'>('loading');
	let errorMessage = $state('');
	let allFits = $state<DegradationFit[]>([]);

	const revealTracker = new RevealTracker();
	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	$effect(() => {
		// Re-subscribes whenever a NEW `controller` prop arrives (a session
		// switch in the harness passes a fresh controller) — returning the
		// unsubscribe from inside the effect, rather than subscribing once at
		// module init, is what makes that reactive.
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	async function load(key: string): Promise<void> {
		status = 'loading';
		allFits = [];
		revealTracker.reset();
		try {
			const { data, error } = await api.GET('/sessions/{key}/degradation', {
				params: { path: { key } }
			});
			if (error) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			allFits = data;
			status = 'ready';
		} catch {
			status = 'error';
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	$effect(() => {
		void load(apiKey);
	});

	function fitKey(fit: DegradationFit): string {
		return `${fit.driver}:${fit.stint_number}:${fit.compound}`;
	}

	function fitLabel(fit: DegradationFit): string {
		return `${fit.driver} · stint ${fit.stint_number} · ${fit.compound}`;
	}

	// Reading `visible.events` (a tracked `$state`) is what makes this
	// `$derived` recompute on every newly-released envelope; feeding it to
	// `revealTracker.update` first means the filter below always runs
	// against the latest reveal state.
	let revealedFits = $derived.by(() => {
		if (!gateReveals) return allFits;
		revealTracker.update(visible.events);
		return allFits.filter((fit) => revealTracker.isStintRevealed(fit.driver, fit.stint_number));
	});

	let selectedAKey = $state<string | null>(null);
	let selectedBKey = $state<string | null>(null);

	let selectedA = $derived(
		revealedFits.find((f) => fitKey(f) === selectedAKey) ?? revealedFits[0] ?? null
	);
	let selectedB = $derived(
		revealedFits.find((f) => fitKey(f) === selectedBKey) ??
			revealedFits.find((f) => f !== selectedA) ??
			null
	);

	function onSelectA(event: Event): void {
		selectedAKey = (event.currentTarget as HTMLSelectElement).value;
	}
	function onSelectB(event: Event): void {
		selectedBKey = (event.currentTarget as HTMLSelectElement).value;
	}

	let maxAge = $derived(Math.max(selectedA?.n_laps_used ?? 0, selectedB?.n_laps_used ?? 0, 1) + 1);

	let curveA = $derived(selectedA ? degradationCurve(selectedA, maxAge) : null);
	let curveB = $derived(
		selectedB && selectedB !== selectedA ? degradationCurve(selectedB, maxAge) : null
	);
	let crossover = $derived(
		selectedA && selectedB && selectedB !== selectedA
			? degradationCrossover(selectedA, selectedB, maxAge)
			: null
	);

	const VB_W = 320;
	const VB_H = 200;
	const PAD = { top: 12, right: 12, bottom: 28, left: 42 };

	let paceDomain = $derived.by(() => {
		const values: number[] = [];
		for (const c of [curveA, curveB]) {
			if (!c) continue;
			values.push(...c.central, ...c.low, ...c.high);
		}
		if (values.length === 0) return { min: 0, max: 1 };
		return { min: Math.min(...values), max: Math.max(...values) };
	});

	function xFor(age: number): number {
		return PAD.left + (age / maxAge) * (VB_W - PAD.left - PAD.right);
	}
	function yFor(pace: number): number {
		const { min, max } = paceDomain;
		const span = Math.max(max - min, 0.01);
		// faster (lower pace) plots higher on screen
		return PAD.top + (1 - (pace - min) / span) * (VB_H - PAD.top - PAD.bottom);
	}
	function linePath(curve: CurveSeries): string {
		return curve.age
			.map(
				(age, i) =>
					`${i === 0 ? 'M' : 'L'} ${xFor(age).toFixed(1)} ${yFor(curve.central[i]!).toFixed(1)}`
			)
			.join(' ');
	}
	function bandPath(curve: CurveSeries): string {
		const top = curve.age.map((age, i) => [xFor(age), yFor(curve.high[i]!)] as const);
		const bottom = curve.age.map((age, i) => [xFor(age), yFor(curve.low[i]!)] as const).reverse();
		return (
			[...top, ...bottom]
				.map(([x, y], i) => `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)
				.join(' ') + ' Z'
		);
	}
</script>

<Panel
	title="Degradation curves"
	subtitle="Fuel-corrected pace vs tyre age. Revealed only once a stint has fully finished on your delayed clock."
>
	{#if status === 'loading'}
		<LoadingState label="Loading degradation fits…" />
	{:else if status === 'error'}
		<p class="hint">{errorMessage}</p>
	{:else if allFits.length === 0}
		<p class="hint">No degradation fits for this session yet.</p>
	{:else if revealedFits.length === 0}
		<p class="hint">
			Nothing revealed yet — the first stint hasn't finished within your delay window. Keep
			watching.
		</p>
	{:else}
		<div class="selectors">
			<label>
				<span>Curve A</span>
				<select onchange={onSelectA}>
					{#each revealedFits as fit (fitKey(fit))}
						<option
							value={fitKey(fit)}
							selected={selectedA !== null && fitKey(fit) === fitKey(selectedA)}
						>
							{fitLabel(fit)}
						</option>
					{/each}
				</select>
			</label>
			{#if revealedFits.length > 1}
				<label>
					<span>Curve B</span>
					<select onchange={onSelectB}>
						{#each revealedFits as fit (fitKey(fit))}
							<option
								value={fitKey(fit)}
								selected={selectedB !== null && fitKey(fit) === fitKey(selectedB)}
							>
								{fitLabel(fit)}
							</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>

		<svg
			viewBox="0 0 {VB_W} {VB_H}"
			class="chart"
			role="img"
			aria-label="Fuel-corrected pace vs tyre age"
		>
			<line
				x1={PAD.left}
				y1={VB_H - PAD.bottom}
				x2={VB_W - PAD.right}
				y2={VB_H - PAD.bottom}
				class="axis"
			/>
			<line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={VB_H - PAD.bottom} class="axis" />
			<text x={PAD.left} y={VB_H - 6} class="axis-label">tyre age (laps) →</text>
			<text x="4" y={PAD.top} class="axis-label">faster</text>

			{#if curveA}
				<path d={bandPath(curveA)} class="ci-band curve-a" />
				<path
					d={linePath(curveA)}
					class="curve curve-a"
					stroke-dasharray={selectedA?.reliability === 'good' ? undefined : '6 4'}
				/>
			{/if}
			{#if curveB}
				<path d={bandPath(curveB)} class="ci-band curve-b" />
				<path
					d={linePath(curveB)}
					class="curve curve-b"
					stroke-dasharray={selectedB?.reliability === 'good' ? undefined : '6 4'}
				/>
			{/if}
			{#if crossover}
				<circle
					cx={xFor(crossover.age)}
					cy={yFor(crossover.value)}
					r="4"
					class="crossover-marker"
				/>
				<text x={xFor(crossover.age) + 6} y={yFor(crossover.value) - 6} class="crossover-label">
					crossover
				</text>
			{/if}
		</svg>

		<div class="legend">
			{#if selectedA}
				<div class="legend-row">
					<span class="swatch curve-a"></span>
					<span>{fitLabel(selectedA)}</span>
					<ConfidenceBadge level={selectedA.reliability} />
					<Estimate
						label="Deg rate"
						value={selectedA.deg_rate_s_per_lap}
						unit="s/lap"
						ciLow={selectedA.deg_rate_ci_low_s_per_lap}
						ciHigh={selectedA.deg_rate_ci_high_s_per_lap}
						sampleSize={selectedA.n_laps_used}
						method="robust regression"
						format={formatSigned}
						size="md"
					/>
				</div>
			{/if}
			{#if selectedB && selectedB !== selectedA}
				<div class="legend-row">
					<span class="swatch curve-b"></span>
					<span>{fitLabel(selectedB)}</span>
					<ConfidenceBadge level={selectedB.reliability} />
					<Estimate
						label="Deg rate"
						value={selectedB.deg_rate_s_per_lap}
						unit="s/lap"
						ciLow={selectedB.deg_rate_ci_low_s_per_lap}
						ciHigh={selectedB.deg_rate_ci_high_s_per_lap}
						sampleSize={selectedB.n_laps_used}
						method="robust regression"
						format={formatSigned}
						size="md"
					/>
				</div>
			{/if}
			{#if revealedFits.length === 1}
				<p class="hint">Only one stint revealed so far — comparison needs a second.</p>
			{/if}
		</div>
	{/if}
</Panel>

<style>
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
		min-width: 10rem;
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
		font-size: 7px;
		fill: var(--color-text-faint);
	}

	.curve {
		fill: none;
		stroke-width: 2.5;
	}

	.curve-a {
		stroke: var(--text-primary);
	}

	.curve-b {
		stroke: var(--text-secondary);
	}

	.ci-band.curve-a {
		fill: color-mix(in srgb, var(--text-primary) 16%, transparent);
		stroke: none;
	}

	.ci-band.curve-b {
		fill: color-mix(in srgb, var(--text-secondary) 16%, transparent);
		stroke: none;
	}

	.crossover-marker {
		fill: var(--color-bg-elevated);
		stroke: var(--color-text);
		stroke-width: 1.5;
	}

	.crossover-label {
		font-size: 8px;
		fill: var(--color-text-muted);
	}

	.legend {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.legend-row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		font-size: var(--font-size-sm);
		flex-wrap: wrap;
	}

	.swatch {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 50%;
		flex: none;
	}

	.swatch.curve-a {
		background: var(--text-primary);
	}

	.swatch.curve-b {
		background: var(--text-secondary);
	}
</style>
