<!--
  "If he boxes now" (ROADMAP W5): where the focused car comes out if it
  boxes at the end of this lap, drawn against how much one stop varies at
  this circuit. Every number is the kernel's own projection (`stripModel`),
  released through Delay Sync with the lap's snapshot. Drawn on the pit
  board's dark surface, wherever it sits.
-->
<script lang="ts">
	import ConfBadge from '$lib/desk/ConfBadge.svelte';
	import { colorForTeam } from '$lib/teams';
	import type { StripModel } from './rejoin';

	interface Props {
		model: StripModel;
	}

	let { model }: Props = $props();

	const W = 380;
	const H = 112;
	const PAD = 14;
	const AXIS_Y = 56;
	const x = (s: number) =>
		PAD + ((s - model.domain.lo) / (model.domain.hi - model.domain.lo)) * (W - 2 * PAD);
	let sigma = $derived(model.sigmaS);
	let neighbours = $derived([model.ahead, model.behind].filter((c) => c !== null));
	let key = $derived(
		[
			"Seconds behind this lap's crossing.",
			`A stop costs ${model.pitLossS.toFixed(1)} s here${
				sigma !== null
					? `; the bands are one and two times the ${sigma.toFixed(1)} s it varies by.`
					: '.'
			}`,
			model.caveat
		]
			.filter(Boolean)
			.join(' ')
	);
</script>

<div class="rejoin" data-testid="rejoin-strip">
	<div class="caps">{model.kicker}</div>
	<p class="rejoin-head">{model.headline}</p>
	<p class="rejoin-sub">{model.sub}</p>
	<svg viewBox="0 0 {W} {H}" role="img" aria-label={model.label}>
		<line
			x1={PAD}
			x2={W - PAD}
			y1={AXIS_Y}
			y2={AXIS_Y}
			stroke="var(--board-rule)"
			stroke-width="2"
		/>
		{#if sigma !== null}
			<rect
				x={x(model.pitLossS - 2 * sigma)}
				y={AXIS_Y - 16}
				width={x(model.pitLossS + 2 * sigma) - x(model.pitLossS - 2 * sigma)}
				height="32"
				rx="4"
				fill="var(--board-band)"
				opacity="0.55"
			/>
			<rect
				x={x(model.pitLossS - sigma)}
				y={AXIS_Y - 16}
				width={x(model.pitLossS + sigma) - x(model.pitLossS - sigma)}
				height="32"
				fill="var(--board-band)"
			/>
		{/if}
		{#each neighbours as car (car.driver)}
			<circle cx={x(car.atS)} cy={AXIS_Y} r="6" fill={colorForTeam(car.team)} />
			<text
				x={x(car.atS)}
				y={AXIS_Y + 38}
				text-anchor="middle"
				font-family="var(--f-mono)"
				font-size="11"
				fill="var(--board-muted)">{car.label}</text
			>
		{/each}
		<circle
			cx={x(model.out.atS)}
			cy={AXIS_Y}
			r="7"
			fill={colorForTeam(model.out.team)}
			stroke="var(--board-ink)"
			stroke-width="2"
		/>
		<text
			x={x(model.out.atS)}
			y={AXIS_Y - 28}
			text-anchor="middle"
			font-family="var(--f-mono)"
			font-size="11"
			fill="var(--board-ink)">{model.out.label} out</text
		>
		{#if model.moreAhead > 0}
			<text
				x={PAD}
				y={AXIS_Y - 44}
				font-family="var(--f-mono)"
				font-size="10"
				fill="var(--board-muted)">← {model.moreAhead} more ahead</text
			>
		{/if}
		{#each model.ticks as t (t)}
			<text
				x={x(t)}
				y={AXIS_Y + 20}
				text-anchor="middle"
				font-family="var(--f-mono)"
				font-size="9"
				fill="var(--board-muted)">{t}s</text
			>
		{/each}
	</svg>
	<p class="rejoin-key">{key}</p>
	<div class="rejoin-conf">
		<ConfBadge level={model.confidence} />
		{#if model.reason}<span>{model.reason}</span>{/if}
	</div>
</div>

<style>
	.rejoin svg {
		display: block;
		width: 100%;
		height: auto;
		margin-top: 8px;
		overflow: visible;
	}
	.rejoin-head {
		margin: 4px 0 0;
		font: 500 26px/1.15 var(--f-display);
		letter-spacing: -0.015em;
		color: var(--board-ink);
	}
	.rejoin-sub {
		margin: 4px 0 0;
		font-size: 13.5px;
		color: var(--board-muted);
	}
	.rejoin-key {
		margin: 2px 0 0;
		font-size: 11px;
		color: var(--board-muted);
	}
	.rejoin-conf {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
		margin-top: 8px;
		font-size: 12px;
		color: var(--board-muted);
	}
	.rejoin-conf :global(.conf) {
		background: transparent;
		border-color: var(--board-rule);
		color: var(--board-ink);
	}
</style>
