<!--
  A placeholder block that holds a panel's final size while its data loads,
  so the page never jumps when it arrives (W0's layout-shift budget).
-->
<script lang="ts">
	interface Props {
		width?: string;
		height?: string;
		radius?: string;
		/** Stacked lines instead of one block. */
		lines?: number;
		label?: string;
	}

	let {
		width = '100%',
		height = '1rem',
		radius = 'var(--radius-sm)',
		lines = 1,
		label
	}: Props = $props();
</script>

<span class="skeleton" role={label ? 'status' : undefined} aria-label={label} aria-busy="true">
	{#each { length: lines } as _, i (i)}
		<span
			class="bone"
			style="width: {lines > 1 && i === lines - 1
				? '60%'
				: width}; height: {height}; border-radius: {radius}"
		></span>
	{/each}
</span>

<style>
	.skeleton {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.bone {
		display: block;
		background: var(--panel-sunk);
	}
	@media (prefers-reduced-motion: no-preference) {
		.bone {
			background: linear-gradient(
				90deg,
				var(--panel-sunk) 0%,
				var(--rule) 50%,
				var(--panel-sunk) 100%
			);
			background-size: 200% 100%;
			animation: shimmer 1.4s linear infinite;
		}
	}
	@keyframes shimmer {
		from {
			background-position: 100% 0;
		}
		to {
			background-position: -100% 0;
		}
	}
</style>
