<script lang="ts">
	import ConfidenceBadge from './ConfidenceBadge.svelte';

	type Tone = 'default' | 'positive' | 'negative';
	type ConfidenceLevel = 'good' | 'thin' | 'unreliable' | 'high' | 'medium' | 'low';

	interface Props {
		label: string;
		/** null renders an em-dash — an explicitly unknown value is never
		 * presented as a zero or blank, per the kernel's own "unknown is not
		 * excluded" discipline. */
		value: string | number | null;
		unit?: string;
		tone?: Tone;
		confidence?: ConfidenceLevel;
		size?: 'md' | 'lg';
	}

	let { label, value, unit, tone = 'default', confidence, size = 'md' }: Props = $props();

	let display = $derived(value === null || value === undefined ? '—' : value);
</script>

<div class="stat stat-{size}">
	<span class="stat-label">{label}</span>
	<span class="stat-value-row">
		<span class="stat-value tone-{tone} font-mono">{display}</span>
		{#if unit && value !== null}
			<span class="stat-unit">{unit}</span>
		{/if}
		{#if confidence}
			<ConfidenceBadge level={confidence} />
		{/if}
	</span>
</div>

<style>
	.stat {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.stat-label {
		font-size: var(--font-size-xs);
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--color-text-muted);
	}

	.stat-value-row {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.stat-value {
		font-weight: 600;
		line-height: var(--line-height-tight);
	}

	.stat-md .stat-value {
		font-size: var(--font-size-xl);
	}

	.stat-lg .stat-value {
		font-size: var(--font-size-3xl);
	}

	.stat-unit {
		font-size: var(--font-size-sm);
		color: var(--color-text-muted);
	}

	.tone-default {
		color: var(--color-text);
	}
	.tone-positive {
		color: var(--color-positive);
	}
	.tone-negative {
		color: var(--color-negative);
	}
</style>
