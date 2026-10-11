<!--
  A circuit drawn from its real outline (the circuit file's own points,
  never a hand-drawn shape). With `draw`, the line traces itself in once
  when it first appears, unless the viewer asked for less motion.
-->
<script lang="ts">
	import type { Point } from '$lib/track-map/types';
	import { outlinePath } from './geometry';

	interface Props {
		points: readonly Point[];
		/** The circuit's name, for screen readers. */
		label: string;
		width?: number;
		height?: number;
		draw?: boolean;
		strokeWidth?: number;
	}

	let { points, label, width = 160, height = 110, draw = false, strokeWidth = 2 }: Props = $props();

	let path = $derived(outlinePath(points, width, height, strokeWidth * 3));
</script>

<svg
	class="track-thumbnail"
	class:draw
	viewBox="0 0 {width} {height}"
	role="img"
	aria-label={label}
>
	{#if path}
		<path class="bed" d={path} stroke-width={strokeWidth * 4}></path>
		<path class="line" d={path} stroke-width={strokeWidth} pathLength="1"></path>
	{/if}
</svg>

<style>
	.track-thumbnail {
		display: block;
		width: 100%;
		height: auto;
	}
	path {
		fill: none;
		stroke-linejoin: round;
		stroke-linecap: round;
	}
	.bed {
		stroke: var(--track);
	}
	.line {
		stroke: var(--accent);
	}
	@media (prefers-reduced-motion: no-preference) {
		.draw .line {
			stroke-dasharray: 1;
			stroke-dashoffset: 1;
			animation: trace 1.6s var(--ease-in-out) forwards;
		}
	}
	@keyframes trace {
		to {
			stroke-dashoffset: 0;
		}
	}
</style>
