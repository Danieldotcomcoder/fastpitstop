<!--
  A tyre compound disc with the laps on the set beside it. The letter is
  always shown, so the compound never depends on colour alone.
-->
<script lang="ts">
	import { compoundLetter, compoundName } from '$lib/desk/format';

	interface Props {
		compound: string | null;
		/** Laps on this set; `null` when the feed hasn't said. */
		age?: number | null;
		size?: number;
	}

	let { compound, age = null, size = 18 }: Props = $props();

	let letter = $derived(compoundLetter(compound));
	let name = $derived(compoundName(compound));
	let label = $derived(age === null ? name : `${name}, ${age} ${age === 1 ? 'lap' : 'laps'} old`);
</script>

<span class="tyre-badge" aria-label={label} title={label}>
	<span class="disc {letter}" style="--size: {size}px" aria-hidden="true">{letter}</span>
	{#if age !== null}<span class="age num" aria-hidden="true">{age}</span>{/if}
</span>

<style>
	.tyre-badge {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		white-space: nowrap;
	}
	.disc {
		width: var(--size);
		height: var(--size);
		border-radius: 50%;
		display: inline-grid;
		place-items: center;
		font: 700 calc(var(--size) * 0.52) / 1 var(--font-mono);
		box-shadow: inset 0 0 0 2px var(--tyre-ring);
		background: var(--panel-sunk);
		color: var(--ink);
	}
	.S {
		background: var(--tyre-soft);
		color: #fff;
	}
	.M {
		background: var(--tyre-medium);
		color: #16191c;
	}
	.H {
		background: var(--tyre-hard);
		color: #16191c;
	}
	.I {
		background: var(--tyre-inter);
		color: #fff;
	}
	.W {
		background: var(--tyre-wet);
		color: #fff;
	}
	.age {
		min-width: 2ch;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		color: var(--ink-2);
	}
</style>
