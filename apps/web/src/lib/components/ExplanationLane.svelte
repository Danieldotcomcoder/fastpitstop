<script lang="ts">
	/** The live/replay "why" lane — one-line explanations as anomalies
	 * occur, Delay-Synced like everything else on the dashboard. Driven
	 * ENTIRELY from `controller.visibleState` via `ExplanationLaneReducer`:
	 * an `explanation` envelope's own pacing is already gated on
	 * `anomaly_time`, so by the time one appears in `visibleState.events`
	 * it is provably not ahead of the user's playback clock. */
	import { EMPTY_VISIBLE_STATE, type DelaySyncController, type VisibleSessionState } from '$lib/delay-sync';
	import { ExplanationLaneReducer } from '$lib/why';
	import ExplanationCard from './ExplanationCard.svelte';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();

	const reducer = new ExplanationLaneReducer();

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	$effect(() => {
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	$effect(() => {
		void controller;
		reducer.reset();
	});

	let explanations = $derived.by(() => {
		reducer.update(visible.events);
		return reducer.explanations;
	});

	let scrollEl: HTMLDivElement | undefined = $state();
	let count = $derived(explanations.length);
	$effect(() => {
		if (count > 0 && scrollEl) scrollEl.scrollLeft = scrollEl.scrollWidth;
	});
</script>

<Panel
	title="Why"
	subtitle="Grounded explanations at the moment they happen — never ahead of your clock"
>
	{#if explanations.length === 0}
		<LoadingState label="Waiting for explained moments…" />
	{:else}
		<div class="lane" bind:this={scrollEl} data-testid="explanation-lane">
			{#each explanations as item (item.anomalyId)}
				<div class="lane-item">
					<ExplanationCard explanation={item} />
				</div>
			{/each}
		</div>
	{/if}
</Panel>

<style>
	.lane {
		display: flex;
		gap: var(--space-3);
		overflow-x: auto;
		padding-bottom: var(--space-2);
		scroll-behavior: smooth;
	}

	.lane-item {
		flex: none;
		width: min(90vw, 22rem);
	}

	@media (prefers-reduced-motion: reduce) {
		.lane {
			scroll-behavior: auto;
		}
	}
</style>
