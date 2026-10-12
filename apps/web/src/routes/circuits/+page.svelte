<script lang="ts">
	import { resolve } from '$app/paths';
	import { seoFor } from '$lib/seo';
	import { TrackThumbnail } from '$lib/ui';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>{seoFor('/circuits').title}</title>
</svelte:head>

<article class="circuits wide-dashboard">
	<header>
		<p class="kicker">Circuits</p>
		<h1>Every circuit, measured</h1>
		<p class="lede">
			What a pit stop costs, how fast each tyre compound wears, and where the corners and DRS zones
			are, all taken from real sessions rather than copied from a press kit.
		</p>
	</header>

	{#if data.circuits.length === 0}
		<p class="empty">The circuit list can't be loaded right now. Try again in a minute.</p>
	{:else}
		<ul class="grid">
			{#each data.circuits as circuit (circuit.slug)}
				<li>
					<a href={resolve('/circuits/[slug]', { slug: circuit.slug })}>
						{#if circuit.thumb}
							<TrackThumbnail fitted={circuit.thumb} label={`${circuit.name} outline`} />
						{/if}
						<span class="country">{circuit.country}</span>
						<span class="name">{circuit.name}</span>
						<span class="facts num">
							{circuit.lapKm.toFixed(3)} km · pit stop
							{#if circuit.measured}{circuit.pitLossS.toFixed(1)} s{:else}not measured{/if}
						</span>
					</a>
				</li>
			{/each}
		</ul>
	{/if}
</article>

<style>
	.circuits {
		width: 100%;
		max-width: 1180px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	header {
		max-width: 640px;
	}

	.kicker {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted);
		margin: 0;
	}

	h1 {
		font-family: var(--font-display);
		font-size: var(--font-size-2xl);
		line-height: 1.15;
		margin: var(--space-2) 0 var(--space-3);
	}

	.lede {
		color: var(--ink-2);
		margin: 0;
	}

	.grid {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr));
		gap: var(--space-3);
	}

	.grid a {
		display: flex;
		flex-direction: column;
		gap: 2px;
		height: 100%;
		padding: var(--space-3) var(--space-4) var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-md);
		background: var(--panel);
		box-shadow: var(--elev-1);
		color: var(--ink);
		text-decoration: none;
		transition: border-color var(--duration-fast) var(--ease-standard);
	}

	.grid a:hover,
	.grid a:focus-visible {
		border-color: var(--accent);
	}

	.grid a :global(.track-thumbnail) {
		margin-bottom: var(--space-2);
	}

	.country {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted);
	}

	.name {
		font-family: var(--font-display);
		font-size: var(--font-size-lg);
		line-height: 1.2;
	}

	.facts {
		font-size: var(--font-size-sm);
		color: var(--ink-2);
		margin-top: var(--space-1);
	}

	.empty {
		color: var(--ink-2);
	}
</style>
