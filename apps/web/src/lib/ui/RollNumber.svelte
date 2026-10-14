<!--
  A timed value that rolls to its new reading (ROADMAP W2 D4: "times and
  gaps roll to their new values"): the old figure slides up and out, the new
  one slides in. Motion only marks that the value changed; the text is the
  value, so nothing is ever shown that the feed did not send. Under
  `prefers-reduced-motion` it simply swaps.
-->
<script lang="ts">
	interface Props {
		value: string;
		/** Roll only when this changes (defaults to the text itself). */
		key?: unknown;
	}

	let { value, key }: Props = $props();
	let trigger = $derived(key === undefined ? value : key);
</script>

<span class="roll"
	>{#key trigger}<span class="roll-in">{value}</span>{/key}</span
>

<style>
	/* Clipped with clip-path, not overflow, so the figure keeps its
	   baseline and lines up with the text beside it. */
	.roll {
		display: inline-block;
		clip-path: inset(0 -0.5em);
	}
	.roll-in {
		display: inline-block;
	}
	@media (prefers-reduced-motion: no-preference) {
		.roll-in {
			animation: roll-in var(--duration-roll) var(--ease-out);
		}
	}
	@keyframes roll-in {
		from {
			transform: translateY(0.7em);
			opacity: 0;
		}
		to {
			transform: none;
			opacity: 1;
		}
	}
</style>
