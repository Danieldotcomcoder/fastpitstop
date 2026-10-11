<!--
  A gap as a bar: how much of `max` it fills. The number itself is shown
  by the caller; the bar is the picture of it.
-->
<script lang="ts">
	import { gapFraction } from './geometry';

	interface Props {
		gap: number | null;
		max: number;
		color?: string;
		label?: string;
	}

	let { gap, max, color = 'var(--ink-2)', label }: Props = $props();

	let fraction = $derived(gapFraction(gap, max));
</script>

<span
	class="gap-bar"
	role="img"
	aria-label={label ?? (gap === null ? 'Gap unknown' : `${gap.toFixed(1)} seconds`)}
>
	<span class="fill" style="width: {fraction * 100}%; background: {color}"></span>
</span>

<style>
	.gap-bar {
		display: block;
		min-width: 32px;
		height: 4px;
		border-radius: 2px;
		background: var(--panel-sunk);
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		border-radius: 2px;
	}
	@media (prefers-reduced-motion: no-preference) {
		.fill {
			transition: width var(--duration-move) var(--ease-out);
		}
	}
</style>
