<!--
  "Before the weekend" on `/live` (ROADMAP W2): what is measured at the next
  circuit from past races, never this weekend's, so it can't spoil a
  session. A stop's cost on the scale of every measured circuit, tyre wear
  per compound with how much evidence each rests on, and the lap itself.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import type { CircuitBrief } from './circuit-brief';

	interface Props {
		circuit: CircuitBrief;
	}

	let { circuit }: Props = $props();

	const TIER_NOTE = { good: '', thin: 'thin', unreliable: 'one or two stints' } as const;
	let marker = $derived(
		circuit.range && circuit.pitLoss.measured
			? (Number.parseFloat(circuit.pitLoss.value) - circuit.range.low) /
					Math.max(0.1, circuit.range.high - circuit.range.low)
			: null
	);
</script>

<section class="before" aria-labelledby="before-title" data-testid="before-weekend">
	<div class="head">
		<h2 id="before-title">Before the weekend, at {circuit.name}</h2>
		<span class="caps">Measured from past races, not this one</span>
	</div>
	<div class="tiles">
		<div class="tile">
			<div class="caps">What a stop costs</div>
			<div class="big num">{circuit.pitLoss.value}</div>
			<div class="note">
				{circuit.pitLoss.detail}{#if circuit.pitLoss.measured}
					&middot; <span class="tier {circuit.pitLoss.tier}">measured</span>{/if}
			</div>
			{#if marker !== null && circuit.range}
				<div class="scale" aria-hidden="true">
					<span style:left="{Math.min(1, Math.max(0, marker)) * 100}%"></span>
				</div>
				<div class="scale-ends num">
					<span>{circuit.range.low.toFixed(1)} s</span><span>{circuit.range.high.toFixed(1)} s</span
					>
				</div>
				<div class="note">The quickest and slowest measured circuits.</div>
			{/if}
		</div>
		<div class="tile">
			<div class="caps">Tyre wear here, per lap of age</div>
			{#if circuit.wear.length > 0}
				<ul class="wear">
					{#each circuit.wear as row (row.compound)}
						<li class={row.tier}>
							<span class="compound">{row.compound}</span>
							<span class="track"><span style:width="{row.fraction * 100}%"></span></span>
							<span class="num">{row.label}</span>
							{#if TIER_NOTE[row.tier]}<span class="tier {row.tier}">{TIER_NOTE[row.tier]}</span
								>{/if}
						</li>
					{/each}
				</ul>
				<div class="note">
					From good fits in {circuit.wearSeasons}: {circuit.wear
						.map((w) => `${w.fits} ${w.compound}`)
						.join(', ')} stints.
				</div>
			{:else}
				<p class="note">Not measured here yet.</p>
			{/if}
		</div>
		<div class="tile">
			<div class="caps">The lap</div>
			<div class="big num">{circuit.lapKm.toFixed(3)}<small> km</small></div>
			<div class="note">
				{circuit.corners} corners · {circuit.drsZones} DRS zone{circuit.drsZones === 1 ? '' : 's'}
			</div>
			<a href={resolve('/circuits/[slug]', { slug: circuit.slug })}>The circuit in full →</a>
		</div>
	</div>
</section>

<style>
	.before {
		margin-top: var(--space-5);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
	.head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--space-3);
		flex-wrap: wrap;
	}
	h2 {
		margin: 0;
		font: 500 var(--font-size-xl) / 1.2 var(--font-display);
	}
	.caps {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
		gap: var(--space-4);
	}
	.tile {
		padding: var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.big {
		font: 500 2.5rem / 1.1 var(--font-display);
		margin-top: var(--space-1);
	}
	.big small {
		font-size: var(--font-size-md);
		color: var(--muted);
	}
	.note {
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}
	.tier.good {
		color: var(--reliability-good);
	}
	.tier.thin {
		color: var(--reliability-thin);
	}
	.tier.unreliable {
		color: var(--reliability-unreliable);
	}
	.scale {
		position: relative;
		height: 6px;
		margin-top: var(--space-3);
		border-radius: 3px;
		background: var(--panel-sunk);
	}
	.scale span {
		position: absolute;
		top: -4px;
		width: 3px;
		height: 14px;
		margin-left: -1px;
		border-radius: 2px;
		background: var(--accent);
	}
	.scale-ends {
		display: flex;
		justify-content: space-between;
		font-size: var(--font-size-xs);
		color: var(--muted);
	}
	.wear {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
		display: grid;
		gap: var(--space-2);
	}
	.wear li {
		display: grid;
		grid-template-columns: 4.5rem 1fr auto;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--font-size-sm);
	}
	.wear .tier {
		grid-column: 2 / -1;
		font-size: var(--font-size-xs);
	}
	.compound {
		text-transform: capitalize;
	}
	.track {
		height: 8px;
		border-radius: 4px;
		background: var(--panel-sunk);
	}
	.track span {
		display: block;
		height: 100%;
		border-radius: 4px;
		background: var(--accent);
	}
	/* Thin evidence is drawn as an outline, never as a solid bar. */
	.wear li.thin .track span {
		background: transparent;
		box-shadow: inset 0 0 0 1.5px var(--accent);
	}
	.wear li.unreliable .track span {
		background: transparent;
		box-shadow: inset 0 0 0 1px var(--muted);
	}
	a {
		margin-top: var(--space-2);
		font-size: var(--font-size-sm);
	}
</style>
