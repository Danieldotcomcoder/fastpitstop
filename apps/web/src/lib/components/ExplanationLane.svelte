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

	/** Newest first, capped. This lane lives in a narrow rail beside the
	 * timing tower; it used to be a horizontally-scrolled filmstrip
	 * auto-scrolled to the right, which in a ~34rem column showed one and a
	 * half cards with the previous one sliced down the middle — it read as
	 * a rendering bug rather than a feed. A short vertical stack, most
	 * recent at the top, is what a "what just happened" feed should be. */
	const VISIBLE_LIMIT = 4;
	let newestFirst = $derived([...explanations].reverse());
	let shown = $derived(newestFirst.slice(0, VISIBLE_LIMIT));
	let older = $derived(Math.max(0, newestFirst.length - shown.length));
</script>

<Panel
	title="Key moments"
	subtitle="What happened and why, with sources — never ahead of your clock"
>
	{#if explanations.length === 0}
		<LoadingState label="Waiting for explained moments…" />
	{:else}
		<div class="lane" data-testid="explanation-lane">
			{#each shown as item (item.anomalyId)}
				<div class="lane-item">
					<ExplanationCard explanation={item} />
				</div>
			{/each}
			{#if older > 0}
				<p class="older">{older} earlier moment{older === 1 ? '' : 's'} this race</p>
			{/if}
		</div>
	{/if}
</Panel>

<style>
	.lane {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.lane-item {
		min-width: 0;
	}

	.older {
		margin: 0;
		color: var(--text-muted);
		font-size: var(--font-size-xs);
	}
</style>
