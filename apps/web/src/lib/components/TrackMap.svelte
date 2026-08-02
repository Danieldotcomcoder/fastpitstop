<script lang="ts">
	/** The live track map (Phase 3.4): a single Canvas rendering the circuit
	 * outline, sector/mini-sector dominance and every car's position, driven
	 * ENTIRELY from Delay Sync's `visibleState` — never a raw stream. A view
	 * component in the same sense `DelaySlider`/`DelayIndicator` are: the
	 * actual animation/interpolation/dominance logic lives in
	 * `$lib/track-map` (framework-agnostic, unit-tested with no DOM), and
	 * this component is the thin glue — canvas sizing, the render loop, and
	 * `prefers-reduced-motion`. */
	import { onDestroy } from 'svelte';
	import { playbackClockMs, type DelaySyncController } from '$lib/delay-sync';
	import Panel from './Panel.svelte';
	import {
		buildArcLengthTable,
		buildDrawFrame,
		RaceStateReducer,
		TrackMapRenderer,
		type ArcLengthTable,
		type TrackOutline
	} from '$lib/track-map';

	interface Props {
		controller: DelaySyncController;
		outline: TrackOutline | null;
	}

	let { controller, outline }: Props = $props();

	let canvas: HTMLCanvasElement | undefined = $state();
	let container: HTMLDivElement | undefined = $state();
	let reducedMotion = $state(false);

	const reducer = new RaceStateReducer();
	let renderer: TrackMapRenderer | null = null;
	let arcTable: ArcLengthTable | null = null;
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
		const delaySeconds = controller.delayState.get().delaySeconds;
		const renderTsMs = playbackClockMs(Date.now(), delaySeconds);
		const frame = buildDrawFrame(
			outline?.points ?? null,
			arcTable,
			reducer.dominance,
			reducer.raceState,
			renderTsMs,
			{ reducedMotion }
		);
		renderer.draw(frame);
	}

	function loop(): void {
		renderOnce();
		frameHandle = requestAnimationFrame(loop);
	}

	$effect(() => {
		// Re-seed the dominance tracker whenever this session's outline
		// changes (including to/from null) — a fresh arc-length table for a
		// fresh circuit, never carried over from the last session. Built once
		// here rather than per animation frame; `TrackMapRenderer` only ever
		// reads the resulting draw positions, never the table itself.
		arcTable = outline ? buildArcLengthTable(outline.points) : null;
		reducer.setOutline(outline);
	});

	$effect(() => {
		// A different `controller` means a different session: drop
		// everything accumulated for the old one rather than mixing two
		// sessions' cars/dominance together.
		void controller;
		reducer.reset();
	});

	$effect(() => {
		if (!canvas) return;
		const ctx = canvas.getContext('2d');
		if (!ctx) return;
		renderer = new TrackMapRenderer(ctx);

		const observer = new ResizeObserver((entries) => {
			const entry = entries[0];
			if (!entry || !renderer) return;
			const { width, height } = entry.contentRect;
			renderer.resize(width, height, window.devicePixelRatio || 1);
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
			// Non-essential animation only: skip the continuous rAF tween and
			// redraw exactly when new data is released instead of every frame.
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

{#if outline}
	<div class="track-map" bind:this={container} role="img" aria-label="Live track map">
		<canvas bind:this={canvas}></canvas>
	</div>
{:else}
	<Panel title="Track map">
		<p class="hint">No track outline available for this circuit yet.</p>
	</Panel>
{/if}

<style>
	.track-map {
		position: relative;
		width: 100%;
		aspect-ratio: 16 / 9;
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

	.hint {
		color: var(--color-text-muted);
		font-size: var(--font-size-sm);
	}
</style>
