<script lang="ts">
	/** The live/replay radio lane — a timeline strip of team-radio clips as
	 * they occur, Delay-Synced like everything else on the dashboard. Driven
	 * ENTIRELY from `controller.visibleState` via `RadioLaneReducer`: a
	 * `radio_clip` envelope's own pacing is already gated on `captured_at`
	 * (`f1_api.stream.replay`'s `_event_pacing_ts`), so by the time one
	 * appears in `visibleState.events` it is provably not ahead of the
	 * user's playback clock — no separate REST call, no separate reveal
	 * gate, the same reasoning `GapStreamGraph`/`UndercutCountdown` give for
	 * reading `visibleState` directly. */
	import { EMPTY_VISIBLE_STATE, type DelaySyncController, type VisibleSessionState } from '$lib/delay-sync';
	import { RadioLaneReducer } from '$lib/radio';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';
	import RadioClipCard from './RadioClipCard.svelte';

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();

	const reducer = new RadioLaneReducer();

	let visible = $state<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	$effect(() => {
		return controller.visibleState.subscribe((state) => (visible = state));
	});

	$effect(() => {
		// A different controller means a different session: drop every clip
		// accumulated for the old one, the same rule every other view here
		// applies when `controller` is swapped for a new session.
		void controller;
		reducer.reset();
	});

	let clips = $derived.by(() => {
		reducer.update(visible.events);
		return reducer.clips;
	});

	let scrollEl: HTMLDivElement | undefined = $state();
	let clipCount = $derived(clips.length);
	$effect(() => {
		if (clipCount > 0 && scrollEl) scrollEl.scrollLeft = scrollEl.scrollWidth;
	});
</script>

<Panel title="Team radio" subtitle="Driver-pit exchanges, released as they happen — never spoiled">
	{#if clips.length === 0}
		<LoadingState label="Waiting for team radio…" />
	{:else}
		<div class="lane" bind:this={scrollEl} data-testid="radio-lane">
			{#each clips as clip, i (`${clip.driver}-${clip.capturedAt}-${i}`)}
				<div class="lane-item">
					<RadioClipCard {clip} />
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
		width: min(85vw, 20rem);
	}
</style>
