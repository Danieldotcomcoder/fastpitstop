<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';
	import { seoFor } from '$lib/seo';
	import { TyreBadge } from '$lib/ui';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let title = $derived(seoFor(page.url.pathname, [], [data.seo]).title);
	const FITS_NOTE = { good: '', thin: ' · thin', unreliable: ' · too few to trust' } as const;
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<article class="circuit wide-dashboard">
	<header>
		<p class="kicker">
			<a href={resolve('/circuits')}>Circuits</a> · {data.country}
		</p>
		<h1>{data.name}</h1>
		<p class="lede">
			Every number here is measured from real sessions, and says how far it can be trusted.
		</p>
	</header>

	<div class="layout">
		<section class="card lap" aria-labelledby="lap-heading">
			<div class="card-head">
				<h2 id="lap-heading">The lap</h2>
				{#if data.map}
					<p class="legend">
						{#if data.map.drs.length}
							<span><span class="key drs" aria-hidden="true"></span>DRS zones</span>
						{/if}
						<span>numbered corners</span>
						<span><span class="key line" aria-hidden="true"></span>start line</span>
					</p>
				{/if}
			</div>
			{#if data.map}
				<svg
					viewBox="0 0 {data.mapSize.width} {data.mapSize.height}"
					role="img"
					aria-label="{data.name}, with its {data.corners} corners and {data.drsZones} DRS zones"
				>
					<path class="bed" d={data.map.path}></path>
					{#each data.map.drs as zone, i (i)}
						<path class="drs" d={zone}></path>
					{/each}
					<path class="racing" d={data.map.path}></path>
					<g class="corners">
						{#each data.map.corners as corner (corner.label)}
							<circle cx={corner.x} cy={corner.y} r="9"></circle>
							<text x={corner.x} y={corner.y + 3.5}>{corner.label}</text>
						{/each}
					</g>
					<rect
						class="start"
						x={data.map.start.x - 9}
						y={data.map.start.y - 3}
						width="18"
						height="6"
						transform="rotate({data.map.start.angle + 90} {data.map.start.x} {data.map.start.y})"
					></rect>
				</svg>
			{:else}
				<p class="muted">No outline for this circuit yet.</p>
			{/if}
		</section>

		<div class="side">
			<div class="tiles">
				<div class="card tile">
					<p class="kicker">Pit stop costs</p>
					<p class="value num">
						{data.pitLoss.value}
						<ConfidenceBadge level={data.pitLoss.tier} />
					</p>
					<p class="sub">
						{data.pitLoss.detail}{#if data.pitLoss.rank}<br />{data.pitLoss.rank}{/if}
					</p>
				</div>
				<div class="card tile">
					<p class="kicker">Lap</p>
					<p class="value num">{data.lapKm.toFixed(3)} km</p>
					<p class="sub">from car telemetry</p>
				</div>
				<div class="card tile">
					<p class="kicker">Corners</p>
					<p class="value num">{data.corners || '—'}</p>
				</div>
				<div class="card tile">
					<p class="kicker">DRS zones</p>
					{#if data.drsZones > 0}
						<p class="value num">{data.drsZones}</p>
						<p class="sub">used until the end of 2025</p>
					{:else}
						<p class="value num">—</p>
						<p class="sub">none on record here</p>
					{/if}
				</div>
			</div>

			<section class="card" aria-labelledby="wear-heading">
				<h2 id="wear-heading">Tyre wear here</h2>
				{#if data.wear.length}
					<p class="note">
						Time lost per lap of tyre age, fuel-corrected, from the {data.wearSeasons} races.
					</p>
					<ul class="wear">
						{#each data.wear as row (row.compound)}
							<li class={row.tier}>
								<TyreBadge compound={row.compound} size={20} />
								<span class="bar" aria-hidden="true">
									<span
										class="fill {row.compound}"
										style="width: {(row.fraction * 100).toFixed(1)}%"
									></span>
								</span>
								<span class="figure num">
									{row.label}<br /><span class="fits"
										>{row.fits} reliable stint{row.fits === 1 ? '' : 's'}{FITS_NOTE[row.tier]}</span
									>
								</span>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="note">Not measured yet: no dry race here has enough clean stints to fit.</p>
				{/if}
			</section>

			<section class="card races" aria-labelledby="races-heading">
				<h2 id="races-heading" class="kicker">Races here</h2>
				{#if data.races.length}
					<ul>
						{#each data.races as race (`${race.season}-${race.round}`)}
							<li>
								<a
									href={resolve('/sessions/[year]/[round]', {
										year: String(race.season),
										round: String(race.round)
									})}
									aria-label="Watch the {race.season} {race.eventName ?? 'race'} replay"
									>{race.season}</a
								>
							</li>
						{/each}
					</ul>
					<p class="muted">Pick a year to watch its replay, spoiler-free.</p>
				{:else}
					<p class="muted">No replay of a race here yet.</p>
				{/if}
			</section>
		</div>
	</div>
</article>

<style>
	.circuit {
		width: 100%;
		max-width: 1180px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.kicker {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted);
		margin: 0;
	}

	.kicker a {
		color: inherit;
	}

	h1 {
		font-family: var(--font-display);
		font-size: var(--font-size-2xl);
		line-height: 1.1;
		margin: var(--space-1) 0 0;
	}

	.lede {
		margin: var(--space-2) 0 0;
		color: var(--ink-2);
	}

	h2 {
		font-family: var(--font-display);
		font-size: var(--font-size-lg);
		font-weight: 500;
		margin: 0;
	}

	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-4);
		align-items: start;
	}

	@media (min-width: 900px) {
		.layout {
			grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
		}
	}

	.card {
		border: 1px solid var(--rule);
		border-radius: var(--radius-md);
		background: var(--panel);
		box-shadow: var(--elev-1);
		padding: var(--space-4);
	}

	.card-head {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: baseline;
		gap: var(--space-1) var(--space-3);
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0 var(--space-3);
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--muted);
	}

	.key {
		display: inline-block;
		width: 18px;
		height: 5px;
		vertical-align: 2px;
		margin-right: 5px;
	}

	.key.drs {
		background: var(--pb);
	}

	.key.line {
		width: 12px;
		background: var(--ink);
	}

	svg {
		display: block;
		width: 100%;
		height: auto;
		margin-top: var(--space-2);
	}

	svg path {
		fill: none;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.bed {
		stroke: var(--track);
		stroke-width: 12;
	}

	.drs {
		stroke: var(--pb);
		stroke-width: 7;
		opacity: 0.85;
	}

	.racing {
		stroke: var(--accent);
		stroke-width: 2;
	}

	.corners circle {
		fill: var(--panel);
		stroke: var(--rule-strong);
	}

	.corners text {
		font: 500 9.5px var(--font-mono);
		text-anchor: middle;
		fill: var(--ink);
	}

	.start {
		fill: var(--ink);
	}

	/* On a phone the map is too small for 20 numbered discs. */
	@media (max-width: 599px) {
		.corners {
			display: none;
		}
		.legend {
			display: none;
		}
	}

	.side {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.tiles {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--space-3);
	}

	.tile {
		padding: var(--space-3) var(--space-4);
	}

	.tile .value {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-1) 0 0;
		font-family: var(--font-display);
		font-size: var(--num-lg);
		line-height: 1.1;
	}

	.tile .sub {
		margin: var(--space-1) 0 0;
		font-size: var(--font-size-xs);
		color: var(--muted);
	}

	.note {
		margin: var(--space-1) 0 var(--space-2);
		font-size: var(--font-size-sm);
		color: var(--muted);
	}

	.wear {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.wear li {
		display: grid;
		grid-template-columns: 26px minmax(0, 1fr) auto;
		gap: var(--space-3);
		align-items: center;
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}

	.bar {
		display: block;
		height: 8px;
		border-radius: 4px;
		background: var(--panel-sunk);
	}

	.fill {
		display: block;
		height: 8px;
		border-radius: 4px;
		background: var(--muted);
	}

	.fill.soft {
		--c: var(--tyre-soft);
	}
	.fill.medium {
		--c: var(--tyre-medium);
	}
	.fill.hard {
		--c: var(--tyre-hard);
	}
	.fill {
		background: var(--c, var(--muted));
	}

	/* Thin evidence is drawn lighter and dashed; one or two stints is an
	   outline only: shown, never passed off as a measurement. */
	.thin .fill {
		opacity: 0.55;
		outline: 1px dashed var(--c);
		outline-offset: 1px;
	}

	.unreliable .fill {
		background: transparent;
		outline: 1px dashed var(--c);
		outline-offset: -1px;
	}

	.figure {
		font: 500 var(--font-size-sm) / 1.3 var(--font-mono);
		text-align: right;
	}

	.fits {
		font-size: var(--font-size-xs);
		color: var(--muted);
	}

	.thin .fits,
	.unreliable .fits {
		color: var(--reliability-thin);
	}

	.races {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.races ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.races a {
		display: inline-block;
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--rule-strong);
		border-radius: var(--radius-sm);
		font: 500 var(--font-size-sm) / 1.4 var(--font-mono);
		color: var(--ink);
		text-decoration: none;
	}

	.races a:hover,
	.races a:focus-visible {
		border-color: var(--accent);
		color: var(--accent);
	}

	.muted {
		margin: 0;
		font-size: var(--font-size-sm);
		color: var(--muted);
	}
</style>
