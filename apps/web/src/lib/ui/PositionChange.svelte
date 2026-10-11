<!--
  Places gained or lost since the start: ▲3, ▼2, or a dash for none. Shape
  and sign carry the meaning, so it never depends on red and green alone.
-->
<script lang="ts">
	import { positionDelta } from './format';

	interface Props {
		start: number | null;
		now: number | null;
	}

	let { start, now }: Props = $props();

	let delta = $derived(positionDelta(start, now));
	let label = $derived.by(() => {
		switch (delta.kind) {
			case 'gain':
				return `Gained ${delta.places} ${delta.places === 1 ? 'place' : 'places'}`;
			case 'loss':
				return `Lost ${delta.places} ${delta.places === 1 ? 'place' : 'places'}`;
			case 'same':
				return 'No change since the start';
			default:
				return 'Start position unknown';
		}
	});
</script>

<span class="position-change num {delta.kind}" aria-label={label} title={label}>
	{#if delta.kind === 'gain'}▲{delta.places}{:else if delta.kind === 'loss'}▼{delta.places}{:else}–{/if}
</span>

<style>
	.position-change {
		display: inline-block;
		min-width: 3ch;
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		font-size: var(--font-size-xs);
		font-weight: 600;
		color: var(--muted);
	}
	.gain {
		color: var(--gain);
	}
	.loss {
		color: var(--loss);
	}
</style>
