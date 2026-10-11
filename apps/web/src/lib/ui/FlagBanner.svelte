<!--
  A full-width banner while the race is not running normally: yellow,
  safety car, VSC, red flag, chequered. Nothing on green. A change of flag
  sweeps in from the left (unless the viewer asked for less motion).
  Feed it the released track status only, so it can't run ahead of the TV.
-->
<script lang="ts">
	import type { TrackStatusTone } from '$lib/desk/race-feed';
	import { bannerFor } from './flags';

	interface Props {
		tone: TrackStatusTone;
		/** A line of detail, e.g. "since lap 28". */
		detail?: string | null;
	}

	let { tone, detail = null }: Props = $props();

	let banner = $derived(bannerFor(tone));
</script>

<div class="flag-slot" role="status" aria-live="polite">
	{#if banner}
		{#key banner.tone}
			<div class="flag-banner {banner.tone}">
				<span class="label">{banner.label}</span>
				{#if detail}<span class="detail">{detail}</span>{/if}
			</div>
		{/key}
	{/if}
</div>

<style>
	.flag-banner {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 2px var(--space-3);
		padding: 8px var(--space-4);
		border-radius: var(--radius-md);
		font: 600 var(--font-size-sm) / 1.2 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
	}
	.detail {
		font-weight: 400;
		letter-spacing: 0.04em;
		text-transform: none;
		opacity: 0.85;
	}
	.yellow {
		background: var(--status-yellow);
		color: var(--status-yellow-ink);
	}
	.sc {
		background: var(--status-sc);
		color: var(--status-sc-ink);
	}
	/* VSC is striped so it reads differently from a full safety car even
	   where the two fills look alike. */
	.vsc {
		background: repeating-linear-gradient(
			-45deg,
			var(--status-vsc) 0 14px,
			color-mix(in srgb, var(--status-vsc) 78%, #fff) 14px 28px
		);
		color: var(--status-vsc-ink);
	}
	.red {
		background: var(--status-red);
		color: var(--status-red-ink);
	}
	.chequered {
		background: var(--status-chequered);
		color: var(--status-chequered-ink);
	}
	@media (prefers-reduced-motion: no-preference) {
		.flag-banner {
			animation: sweep var(--duration-sweep) var(--ease-out);
		}
	}
	@keyframes sweep {
		from {
			clip-path: inset(0 100% 0 0);
		}
		to {
			clip-path: inset(0 0 0 0);
		}
	}
</style>
