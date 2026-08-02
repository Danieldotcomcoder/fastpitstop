<script lang="ts">
	/** The "feel the race" chart: every driver's gap-to-leader flowing as a
	 * colored band over race laps, so undercut swings and DRS trains read as
	 * shapes rather than a table of numbers. Driven ENTIRELY from
	 * `controller.visibleState` via `GapStreamReducer` — no REST call, no
	 * separate spoiler gate, since `gap_updated`/`lap_completed` envelopes
	 * are already delay-gated the moment they appear in `visibleState.events`
	 * (see `$lib/kernel-views/gap-stream.ts`'s module doc). Structurally the
	 * same "framework-agnostic core, thin Svelte glue" split
	 * `TrackMap.svelte` uses over `$lib/track-map`. */
	import { onDestroy } from 'svelte';
	import type { DelaySyncController } from '$lib/delay-sync';
	import { buildGapStreamFrame, GapStreamReducer, GapStreamRenderer } from '$lib/kernel-views';
	import LoadingState from './LoadingState.svelte';
	import Panel from './Panel.svelte';

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();

	let canvas: HTMLCanvasElement | undefined = $state();
	let container: HTMLDivElement | undefined = $state();
	let hasData = $state(false);
	let highlightDriver = $state<string | null>(null);
	let legendDrivers = $state<{ driver: string; code: string | null; color: string }[]>([]);
	let reducedMotion = $state(false);

	const reducer = new GapStreamReducer();
	let renderer: GapStreamRenderer | null = null;
	let resizeObserver: ResizeObserver | null = null;
	let frameHandle: number | null = null;
	let mediaQuery: MediaQueryList | null = null;

	function onReducedMotionChange(): void {
		reducedMotion = mediaQuery?.matches ?? false;
	}

	function renderOnce(): void {
		if (!renderer) return;
		const { events } = controller.visibleState.get();
		reducer.update(events);
		const frame = buildGapStreamFrame(reducer.state);
		hasData = frame.bands.length > 0;
		legendDrivers = frame.bands.map((b) => ({ driver: b.driver, code: b.code, color: b.color }));
		renderer.draw(frame, highlightDriver);
	}

	function loop(): void {
		renderOnce();
		frameHandle = requestAnimationFrame(loop);
	}

	function toggleHighlight(driver: string): void {
		highlightDriver = highlightDriver === driver ? null : driver;
		renderOnce();
	}

	$effect(() => {
		// A different controller means a different session: drop everything
		// accumulated for the old one.
		void controller;
		reducer.reset();
		highlightDriver = null;
	});

	$effect(() => {
		if (!canvas) return;
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		renderer = new GapStreamRenderer(ctx);

		const observer = new ResizeObserver((entries) => {
			const entry = entries[0];
			if (!entry || !renderer) return;
			const { width, height } = entry.contentRect;
			renderer.resize(width, height, window.devicePixelRatio || 1);
			renderOnce();
		});
		if (container) observer.observe(container);
		resizeObserver = observer;

		return () => {
			observer.disconnect();
			resizeObserver = null;
			renderer = null;
		};
	});

	$effect(() => {
		mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
		reducedMotion = mediaQuery.matches;
		mediaQuery.addEventListener('change', onReducedMotionChange);
		return () => mediaQuery?.removeEventListener('change', onReducedMotionChange);
	});

	$effect(() => {
		if (reducedMotion) {
			renderOnce();
			const unsubscribe = controller.visibleState.subscribe(() => renderOnce());
			return unsubscribe;
		}
		frameHandle = requestAnimationFrame(loop);
		return () => {
			if (frameHandle !== null) cancelAnimationFrame(frameHandle);
			frameHandle = null;
		};
	});

	onDestroy(() => {
		if (frameHandle !== null) cancelAnimationFrame(frameHandle);
		resizeObserver?.disconnect();
	});
</script>

<Panel
	title="Gap to leader"
	subtitle="Every driver's gap over race laps — undercuts and DRS trains as shapes"
>
	<div class="gap-stream" bind:this={container}>
		<canvas bind:this={canvas}></canvas>
		{#if !hasData}
			<div class="overlay">
				<LoadingState label="Waiting for gap data…" />
			</div>
		{/if}
	</div>
	{#if legendDrivers.length > 0}
		<div class="legend">
			{#each legendDrivers as d (d.driver)}
				<button
					type="button"
					class="chip"
					class:dimmed={highlightDriver !== null && highlightDriver !== d.driver}
					style:--chip-color={d.color}
					onclick={() => toggleHighlight(d.driver)}
				>
					{d.code ?? d.driver}
				</button>
			{/each}
		</div>
	{/if}
</Panel>

<style>
	.gap-stream {
		position: relative;
		width: 100%;
		aspect-ratio: 4 / 3;
		background: var(--color-bg-elevated);
		border: 1px solid var(--color-border);
		border-radius: var(--radius-lg);
		overflow: hidden;
	}

	canvas {
		display: block;
		width: 100%;
		height: 100%;
	}

	.overlay {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.chip {
		padding: 0.1875rem var(--space-3);
		border-radius: 999px;
		border: 1px solid var(--chip-color);
		background: color-mix(in srgb, var(--chip-color) 16%, var(--color-bg-elevated));
		color: var(--color-text);
		font-size: var(--font-size-xs);
		font-weight: 600;
		font-family: var(--font-mono);
		cursor: pointer;
	}

	.chip.dimmed {
		opacity: 0.4;
	}
</style>
