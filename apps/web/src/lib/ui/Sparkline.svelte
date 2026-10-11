<!--
  A small trend line. Unknown values break the line; `bands` shade index
  ranges (a safety car, say) so a jump in the trend has its reason drawn
  under it.
-->
<script lang="ts">
	import { sparkSegments } from './geometry';

	interface Props {
		values: readonly (number | null)[];
		/** What the line shows, for screen readers. */
		label: string;
		width?: number;
		height?: number;
		domain?: readonly [number, number];
		/** Inclusive [from, to] index ranges to shade. */
		bands?: readonly (readonly [number, number])[];
		color?: string;
	}

	let {
		values,
		label,
		width = 120,
		height = 32,
		domain,
		bands = [],
		color = 'var(--accent)'
	}: Props = $props();

	const PAD = 3;
	let segments = $derived(sparkSegments(values, width, height, PAD, domain));
	let last = $derived.by(() => {
		const tail = segments.at(-1)?.split(' ').at(-1);
		if (!tail || values.at(-1) === null) return null;
		const [x, y] = tail.split(',').map(Number);
		return { x: x!, y: y! };
	});
	let step = $derived(values.length > 1 ? (width - 2 * PAD) / (values.length - 1) : 0);
</script>

<svg
	class="sparkline"
	viewBox="0 0 {width} {height}"
	{width}
	{height}
	role="img"
	aria-label={label}
>
	{#each bands as [from, to] (from)}
		<rect
			class="band"
			x={PAD + Math.max(0, from - 0.5) * step}
			y="0"
			width={Math.max(step, (to - from + 1) * step)}
			{height}
		></rect>
	{/each}
	{#each segments as points (points)}
		<polyline {points} fill="none" stroke={color} stroke-width="2" stroke-linejoin="round"
		></polyline>
	{/each}
	{#if last}<circle cx={last.x} cy={last.y} r="3" fill={color}></circle>{/if}
</svg>

<style>
	.sparkline {
		display: block;
		max-width: 100%;
		overflow: visible;
	}
	.band {
		fill: var(--status-sc);
		opacity: 0.16;
	}
	polyline {
		stroke-linecap: round;
	}
</style>
