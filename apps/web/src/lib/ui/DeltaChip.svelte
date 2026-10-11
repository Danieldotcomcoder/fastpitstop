<!--
  A signed change (`+0.4 s`, `−1.2 s`) that rolls to each new value. Which
  sign is good depends on the number: a shrinking gap is good for the car
  behind, a growing one for the car ahead, so the caller says.
-->
<script lang="ts">
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import { formatDelta } from './format';

	interface Props {
		value: number | null;
		digits?: number;
		unit?: string;
		/** Which sign reads as good (green); `none` keeps the chip neutral. */
		good?: 'positive' | 'negative' | 'none';
	}

	let { value, digits = 1, unit = '', good = 'none' }: Props = $props();

	// Matches --duration-roll in tokens.css.
	const ROLL_MS = 400;
	const shown = new Tween(0, { duration: ROLL_MS, easing: cubicOut });
	$effect(() => {
		if (value === null) return;
		void shown.set(value, { duration: prefersReducedMotion.current ? 0 : ROLL_MS });
	});

	let tone = $derived.by(() => {
		if (value === null || good === 'none' || Number(Math.abs(value).toFixed(digits)) === 0)
			return 'neutral';
		return value > 0 === (good === 'positive') ? 'gain' : 'loss';
	});
	let text = $derived(value === null ? '—' : formatDelta(shown.current, digits, unit));
</script>

<span class="delta-chip num {tone}" aria-label={formatDelta(value, digits, unit)}>{text}</span>

<style>
	.delta-chip {
		display: inline-block;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		font-size: var(--font-size-xs);
		font-weight: 600;
		padding: 2px 6px;
		border-radius: 999px;
		background: var(--panel-sunk);
		color: var(--ink-2);
	}
	.gain {
		color: var(--gain);
		background: color-mix(in srgb, var(--gain) 12%, transparent);
	}
	.loss {
		color: var(--loss);
		background: color-mix(in srgb, var(--loss) 12%, transparent);
	}
</style>
