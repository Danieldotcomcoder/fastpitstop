<!--
  One number, large, with its label and an optional line underneath. A
  kernel value passes its confidence, which is always shown beside it.
-->
<script lang="ts">
	import type { Snippet } from 'svelte';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';

	type ConfidenceLevel = 'good' | 'thin' | 'unreliable' | 'high' | 'medium' | 'low';

	interface Props {
		label: string;
		/** `null` renders an em dash: unknown is never shown as zero. */
		value: string | number | null;
		unit?: string;
		size?: 'hero' | 'xl' | 'lg';
		confidence?: ConfidenceLevel;
		/** A line under the number: context, a trend, a source. */
		sub?: Snippet;
	}

	let { label, value, unit, size = 'xl', confidence, sub }: Props = $props();
</script>

<div class="stat-tile {size}">
	<span class="label">{label}</span>
	<span class="value-row">
		<span class="value num">{value ?? '—'}</span>
		{#if unit && value !== null}<span class="unit">{unit}</span>{/if}
		{#if confidence}<ConfidenceBadge level={confidence} />{/if}
	</span>
	{#if sub}<span class="sub">{@render sub()}</span>{/if}
</div>

<style>
	.stat-tile {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
	}
	.label {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.value-row {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		flex-wrap: wrap;
	}
	.value {
		font-family: var(--font-display);
		font-variant-numeric: lining-nums tabular-nums;
		line-height: 1;
		color: var(--ink);
	}
	.hero .value {
		font-size: var(--num-hero);
	}
	.xl .value {
		font-size: var(--num-xl);
	}
	.lg .value {
		font-size: var(--num-lg);
	}
	.unit {
		font-size: var(--font-size-md);
		color: var(--ink-2);
	}
	.sub {
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}
</style>
