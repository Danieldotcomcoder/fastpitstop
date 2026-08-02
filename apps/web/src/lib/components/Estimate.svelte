<script lang="ts">
	/**
	 * Signature reliability rendering for every derived kernel value.
	 * Hairline width = confidence interval; opacity = sample support.
	 * Below the clean-lap floor, no number — an honest gap.
	 */
	import {
		bandOpacity,
		bandWidthFraction,
		defaultFormat,
		hasEnoughSamples,
		MIN_CLEAN_SAMPLES
	} from '$lib/estimate';

	interface Props {
		label?: string;
		value: number | null;
		unit?: string;
		/** Half-open CI endpoints on the same scale as `value`. */
		ciLow?: number | null;
		ciHigh?: number | null;
		sampleSize: number;
		minSamples?: number;
		/** Plain wording, e.g. "robust regression". */
		method?: string;
		/** Residual / uncertainty note, e.g. "±0.04s/lap". */
		residual?: string | null;
		digits?: number;
		format?: (value: number) => string;
		size?: 'md' | 'lg';
	}

	let {
		label,
		value,
		unit,
		ciLow = null,
		ciHigh = null,
		sampleSize,
		minSamples = MIN_CLEAN_SAMPLES,
		method = 'robust regression',
		residual = null,
		digits = 2,
		format,
		size = 'md'
	}: Props = $props();

	let enough = $derived(hasEnoughSamples(sampleSize, minSamples));
	let display = $derived(
		value === null || !enough ? null : (format ?? ((v) => defaultFormat(v, digits)))(value)
	);
	let widthFrac = $derived(bandWidthFraction(ciLow, ciHigh, value ?? 1));
	let opacity = $derived(bandOpacity(sampleSize, minSamples));

	let tip = $derived.by(() => {
		const bits = [`Fitted from ${sampleSize} clean lap${sampleSize === 1 ? '' : 's'}`];
		if (method) bits.push(method);
		if (residual) bits.push(residual);
		else if (ciLow != null && ciHigh != null && Number.isFinite(ciLow) && Number.isFinite(ciHigh)) {
			const half = Math.abs(ciHigh - ciLow) / 2;
			bits.push(`±${half.toFixed(digits)}${unit ?? ''}`);
		}
		return bits.join(' · ');
	});

	let prevValue = $state<number | null>(null);
	let tick = $state(false);
	$effect(() => {
		if (value === null || !enough) return;
		if (prevValue !== null && prevValue !== value) {
			tick = true;
			const id = window.setTimeout(() => (tick = false), 320);
			prevValue = value;
			return () => window.clearTimeout(id);
		}
		prevValue = value;
	});
</script>

<div
	class="estimate estimate-{size}"
	class:tick
	title={enough ? tip : undefined}
>
	{#if label}
		<span class="label">{label}</span>
	{/if}

	{#if !enough || display === null}
		<span class="gap" role="status">Not enough clean laps yet.</span>
	{:else}
		<span class="value-block">
			<span class="value-row">
				<span class="value font-mono">{display}</span>
				{#if unit}
					<span class="unit">{unit}</span>
				{/if}
			</span>
			<span
				class="band"
				style="--band-width: {widthFrac * 100}%; --band-opacity: {opacity}"
				aria-hidden="true"
			></span>
			<span class="tip visually-hidden">{tip}</span>
		</span>
	{/if}
</div>

<style>
	.estimate {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		position: relative;
		min-width: 0;
	}

	.estimate.tick::before {
		content: '';
		position: absolute;
		left: -0.5rem;
		top: 0.15rem;
		bottom: 0.15rem;
		width: 1px;
		background: var(--text-primary);
		animation: tick-fade var(--duration-crossfade) linear forwards;
	}

	@keyframes tick-fade {
		from {
			opacity: 1;
		}
		to {
			opacity: 0;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.estimate.tick::before {
			animation: none;
			opacity: 0;
		}
	}

	.label {
		font-size: var(--font-size-xs);
		font-weight: 500;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-muted);
	}

	.value-block {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		align-items: flex-start;
	}

	.value-row {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}

	.value {
		font-weight: 500;
		line-height: var(--line-height-tight);
		color: var(--text-primary);
		transition: opacity var(--duration-crossfade) linear;
	}

	.estimate-md .value {
		font-size: var(--font-size-xl);
	}

	.estimate-lg .value {
		font-size: var(--font-size-3xl);
	}

	.unit {
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.band {
		display: block;
		height: 1px;
		width: var(--band-width);
		max-width: 100%;
		min-width: 0.75rem;
		background: color-mix(in srgb, var(--text-primary) calc(var(--band-opacity) * 100%), transparent);
	}

	.gap {
		font-size: var(--font-size-sm);
		color: var(--text-muted);
		font-style: normal;
	}

	/* Native title covers hover; on touch, details sit in visually-hidden for AT. */
	.estimate:focus-within .band,
	.estimate:hover .band {
		height: 2px;
	}
</style>
