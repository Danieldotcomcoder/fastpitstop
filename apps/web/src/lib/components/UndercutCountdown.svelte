<script lang="ts">
	/** The undercut/overcut countdown — Phase 2.5's payoff primitive
	 * rendered as Phase 3's headline view. Reads ONLY
	 * `controller.visibleState.latestSnapshot`: the WebSocket's own per-lap
	 * `kernel_snapshot` envelope (`f1_api.stream.replay.LapSnapshotFeed`) is
	 * already the crossover projection re-derived at the correct decision
	 * lap and released through Delay Sync like any other envelope, so there
	 * is no REST fetch here and none is needed — this is the one view of
	 * the four that needs no separate spoiler gate (`$lib/kernel-views/
	 * reveal`), because there is nothing to gate beyond what Delay Sync
	 * already gates for every envelope. */
	import type { components } from '$lib/api/client';
	import {
		EMPTY_VISIBLE_STATE,
		type DelaySyncController,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { formatSigned } from '$lib/estimate';
	import Estimate from './Estimate.svelte';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';
	import Stat from './Stat.svelte';

	type Crossover = components['schemas']['CrossoverResultOut'];

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();

	/** `KernelSnapshotPayload.crossovers` is deliberately typed `unknown[]`
	 * by Delay Sync ("don't release this before its time, not know what a
	 * crossover means") — light shape validation here is this view's own
	 * job, the same "don't trust a network shape blindly" discipline the
	 * track-map dev harness's `toTrackOutline` follows. */
	function isCrossover(value: unknown): value is Crossover {
		if (typeof value !== 'object' || value === null) return false;
		const v = value as Record<string, unknown>;
		return (
			typeof v.attacker === 'string' &&
			typeof v.defender === 'string' &&
			typeof v.kind === 'string' &&
			typeof v.window === 'string' &&
			typeof v.confidence === 'string'
		);
	}

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	$effect(() => {
		// Re-subscribes whenever a NEW `controller` prop arrives (a session
		// switch in the harness passes a fresh controller) — returning the
		// unsubscribe from inside the effect, rather than subscribing once at
		// module init, is what makes that reactive.
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	let crossovers = $derived((visible.latestSnapshot?.crossovers ?? []).filter(isCrossover));

	function battleKey(c: Crossover): string {
		return `${c.kind}:${c.attacker}:${c.defender}`;
	}

	let selectedKey = $state<string | null>(null);

	let focused = $derived.by((): Crossover | null => {
		if (crossovers.length === 0) return null;
		const match = selectedKey ? crossovers.find((c) => battleKey(c) === selectedKey) : undefined;
		return match ?? crossovers[0] ?? null;
	});

	function onBattleChange(event: Event): void {
		selectedKey = (event.currentTarget as HTMLSelectElement).value;
	}

	function headline(c: Crossover): string {
		const verb = c.kind === 'undercut' ? 'Undercut' : 'Overcut';
		const subject = c.kind === 'undercut' ? c.defender : c.attacker;
		switch (c.window) {
			case 'open':
				return `${verb} on ${subject} is OPEN — pit now`;
			case 'opens_later':
				return c.opens_in_laps !== null
					? `${verb} on ${subject} opens in ${c.opens_in_laps} lap${c.opens_in_laps === 1 ? '' : 's'}`
					: `${verb} on ${subject} opens later`;
			case 'closed':
				return `${verb} on ${subject} is CLOSED`;
			default:
				return `${verb} on ${subject}: UNKNOWN`;
		}
	}

	function windowTone(window: string): 'positive' | 'negative' | 'default' {
		if (window === 'open') return 'positive';
		if (window === 'closed') return 'negative';
		return 'default';
	}

	/** Clean-lap support for the projection — worst of the two stint fits. */
	let fitSamples = $derived.by(() => {
		const c = focused;
		if (!c) return 0;
		const a = c.attacker_state.n_laps_in_fit ?? 0;
		const b = c.defender_state.n_laps_in_fit ?? 0;
		return Math.min(a, b);
	});
</script>

<Panel
	title="Undercut / overcut countdown"
	subtitle="The focused battle, projected from fitted pace over the stop stagger"
>
	{#if crossovers.length === 0}
		<LoadingState label="Waiting for a live battle to project…" />
	{:else if focused}
		{#if crossovers.length > 1}
			<select onchange={onBattleChange}>
				{#each crossovers as c (battleKey(c))}
					<option value={battleKey(c)} selected={battleKey(c) === battleKey(focused)}>
						{c.attacker} vs {c.defender} ({c.kind})
					</option>
				{/each}
			</select>
		{/if}

		<p
			class="headline"
			class:tone-positive={windowTone(focused.window) === 'positive'}
			class:tone-negative={windowTone(focused.window) === 'negative'}
		>
			{headline(focused)}
		</p>

		<div class="stats-row">
			<Estimate
				label="Margin if covered"
				value={focused.projected_gap_if_covered_s}
				unit="s"
				ciLow={focused.projected_gap_if_covered_low_s}
				ciHigh={focused.projected_gap_if_covered_high_s}
				sampleSize={fitSamples}
				method="pace projection over pit stagger"
				format={formatSigned}
			/>
			<Estimate
				label="Rejoin gap"
				value={focused.projected_rejoin_gap_s}
				unit="s"
				sampleSize={fitSamples}
				method="pace projection over pit stagger"
				format={formatSigned}
			/>
			<Stat label="Decision lap" value={focused.decision_lap} />
			<Stat label="Pit loss" value={focused.pit_loss_s.toFixed(1)} unit="s" />
		</div>

		{#if focused.crossover_lap !== null}
			<Estimate
				label="Predicted crossover lap"
				value={focused.crossover_lap}
				sampleSize={fitSamples}
				method="pace projection over pit stagger"
				digits={0}
			/>
		{/if}

		{#if focused.window === 'unknown' || focused.confidence === 'low'}
			<ul class="reasons">
				{#each focused.reasons as reason (reason)}
					<li>{reason}</li>
				{/each}
			</ul>
		{/if}
	{/if}
</Panel>

<style>
	select {
		width: 100%;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
	}

	.headline {
		margin: 0;
		font-family: var(--font-display);
		font-size: var(--font-size-lg);
		font-weight: 600;
		line-height: var(--line-height-tight);
	}

	.tone-positive {
		color: var(--color-positive);
	}

	.tone-negative {
		color: var(--color-negative);
	}

	.stats-row {
		display: flex;
		gap: var(--space-6);
		flex-wrap: wrap;
	}

	.reasons {
		margin: 0;
		padding-left: var(--space-5);
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}
</style>
