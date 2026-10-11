<!--
  A driver's stints so far, as segments of one bar in compound colours.
  Spoiler-safe by construction: `stintSegments` never draws past the lap
  the viewer has reached, and spans the laps run so far when the race
  distance is unknown.
-->
<script lang="ts">
	import { compoundName } from '$lib/desk/format';
	import { stintSegments, type Stint } from './geometry';

	interface Props {
		stints: readonly Stint[];
		currentLap: number;
		totalLaps?: number | null;
		height?: number;
	}

	let { stints, currentLap, totalLaps = null, height = 6 }: Props = $props();

	let segments = $derived(stintSegments(stints, currentLap, totalLaps));
	let label = $derived(
		segments.length === 0
			? 'No stints yet'
			: 'Stints: ' +
					segments
						.map((s) => `${compoundName(s.compound).toLowerCase()} laps ${s.fromLap}–${s.toLap}`)
						.join(', ')
	);

	const KEY: Record<string, string> = {
		soft: 'S',
		medium: 'M',
		hard: 'H',
		intermediate: 'I',
		wet: 'W'
	};
	const keyOf = (compound: string | null) => KEY[(compound ?? '').toLowerCase()] ?? 'U';
</script>

<span class="stint-bar" style="--h: {height}px" role="img" aria-label={label}>
	{#each segments as segment (segment.fromLap)}
		<span
			class="segment {keyOf(segment.compound)}"
			style="left: {segment.left}%; width: {segment.width}%"
			title="{compoundName(segment.compound)}, laps {segment.fromLap}–{segment.toLap}"
		></span>
	{/each}
</span>

<style>
	.stint-bar {
		position: relative;
		display: block;
		height: var(--h);
		min-width: 40px;
		border-radius: calc(var(--h) / 2);
		background: var(--panel-sunk);
		overflow: hidden;
	}
	.segment {
		position: absolute;
		top: 0;
		bottom: 0;
		/* A hairline gap between stints marks each pit stop. */
		box-shadow: inset -1px 0 0 var(--panel);
		background: var(--other-line);
	}
	.S {
		background: var(--tyre-soft);
	}
	.M {
		background: var(--tyre-medium);
	}
	.H {
		background: var(--tyre-hard);
		box-shadow:
			inset 0 0 0 1px var(--rule-strong),
			inset -1px 0 0 var(--panel);
	}
	.I {
		background: var(--tyre-inter);
	}
	.W {
		background: var(--tyre-wet);
	}
</style>
