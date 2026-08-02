<script lang="ts">
	/**
	 * Renders a kernel confidence/reliability signal — never just a bare
	 * number. Accepts both taxonomies the kernel actually emits
	 * (`f1_kernel.Reliability`'s good/thin/unreliable for degradation and
	 * braking fits; `f1_kernel.Confidence`'s high/medium/low for undercut
	 * crossovers) so callers never have to remap one onto the other.
	 * Color carries the tier AND the label text does too — never color alone.
	 */
	type Level = 'good' | 'thin' | 'unreliable' | 'high' | 'medium' | 'low';

	interface Props {
		level: Level;
	}

	let { level }: Props = $props();

	const TIER: Record<Level, 'good' | 'thin' | 'unreliable'> = {
		good: 'good',
		high: 'good',
		thin: 'thin',
		medium: 'thin',
		unreliable: 'unreliable',
		low: 'unreliable'
	};

	let tier = $derived(TIER[level]);
</script>

<span class="badge badge-{tier}">
	<span class="dot" aria-hidden="true"></span>
	{level}
</span>

<style>
	.badge {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		padding: 0.1875rem var(--space-3);
		border-radius: var(--radius-sm);
		border: 1px solid var(--hairline-strong);
		font-family: var(--font-ui);
		font-size: var(--font-size-xs);
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		line-height: 1;
		white-space: nowrap;
	}

	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		flex: none;
	}

	.badge-good {
		color: var(--reliability-good);
		border-color: color-mix(in srgb, var(--reliability-good) 45%, var(--hairline));
	}
	.badge-good .dot {
		background: var(--reliability-good);
	}

	.badge-thin {
		color: var(--reliability-thin);
		border-color: color-mix(in srgb, var(--reliability-thin) 45%, var(--hairline));
	}
	.badge-thin .dot {
		background: var(--reliability-thin);
	}

	.badge-unreliable {
		color: var(--reliability-unreliable);
		border-color: color-mix(in srgb, var(--reliability-unreliable) 45%, var(--hairline));
	}
	.badge-unreliable .dot {
		background: var(--reliability-unreliable);
	}
</style>
