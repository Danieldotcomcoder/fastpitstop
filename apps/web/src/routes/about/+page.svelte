<script lang="ts">
	import { resolve } from '$app/paths';
	import { brand } from '$lib/brand';
	import { PUBLISHED } from '$lib/published';
	import { seoFor } from '$lib/seo';

	const { undercut, pitLoss, fuel, story } = PUBLISHED;
	/** The numbers behind it (ROADMAP W2): each from a committed report,
	 * checked against it by `published.test.ts`. */
	const TILES = [
		{
			value: `${undercut.highConfidenceHitRatePct}%`,
			what: 'of high-confidence undercut calls were right',
			from: `${undercut.highConfidenceCalls} calls on ${undercut.races} races of ${undercut.season} the model never saw`
		},
		{
			value: String(pitLoss.stops),
			what: 'pit stops measured',
			from: `${pitLoss.races} races of ${pitLoss.season}: a stop costs ${pitLoss.lowest.seconds} s at ${pitLoss.lowest.circuit}, ${pitLoss.highest.seconds} s at ${pitLoss.highest.circuit}`
		},
		{
			value: String(fuel.sPerKg),
			what: 'seconds a lap per kilogram of fuel',
			from: `fitted across ${fuel.stints} stints of ${fuel.races} races`
		},
		{
			value: `${story.borneOut} / ${story.cards}`,
			what: 'story cards borne out by the raw timing',
			from: `${story.races} races judged; ${story.found} of ${story.headlines} headline moments found`
		}
	];
</script>

<svelte:head>
	<title>{seoFor('/about').title}</title>
</svelte:head>

<article class="about">
	<h1>About {brand.name}</h1>
	<p>
		{brand.name} is an unofficial, free Formula 1 dashboard for following a session on a second screen
		while you watch it on TV or a stream. It shows live timing, and it also works out what the timing
		feed does not tell you.
	</p>

	<section class="numbers" aria-labelledby="numbers-title">
		<h2 id="numbers-title">The numbers behind it</h2>
		<p class="muted">Each from a report committed with the code, re-run as seasons land.</p>
		<div class="tiles">
			{#each TILES as tile (tile.what)}
				<div class="tile">
					<div class="value num">{tile.value}</div>
					<div class="what">{tile.what}</div>
					<div class="from">{tile.from}</div>
				</div>
			{/each}
		</div>
	</section>

	<h2>What it shows</h2>
	<ul>
		<li>
			<strong>Live timing:</strong> running order, gaps and intervals, last laps, tyre compound and age,
			pit stops and race control messages.
		</li>
		<li>
			<strong>Undercut and overcut predictions:</strong> for the closest battles, whether pitting now
			would get a driver ahead, and on which lap, with a confidence level.
		</li>
		<li>
			<strong>Tyre degradation:</strong> how fast each set of tyres is losing time, fitted from fuel-corrected
			laps, with a verdict on how reliable each fit is.
		</li>
		<li>
			<strong>Why it happened:</strong> one sentence on a notable moment, citing the evidence behind it,
			or an honest "no clear cause found".
		</li>
		<li>
			<strong>Race replays:</strong> past races lap by lap, at any speed, through the same dashboard.
		</li>
	</ul>

	<h2>No spoilers</h2>
	<p>
		A broadcast runs behind the track. Set your delay with the slider, or tap when the lights go
		out, and every number, chart and prediction waits until your picture reaches that moment. The
		page checks your device's clock against the server's, so a phone whose clock is wrong cannot
		release anything early.
	</p>

	<h2>How the numbers are made</h2>
	<p>
		Live data comes from F1's public live timing feed. Historical races come from the community-run
		FastF1 and OpenF1 projects. Constants such as fuel effect and pit-lane time loss are measured
		from past races rather than assumed, and the undercut predictor is tested against races it was
		never tuned on. Anything not yet measured is marked as provisional.
	</p>

	<h2>Unofficial</h2>
	<p>
		{brand.name} is a fan project. It is not associated with Formula 1, the FIA or any team, and uses
		no official logos or broadcast video.
	</p>

	<p class="actions">
		<a href={resolve('/live')}>Open the live dashboard</a>
		<a href={resolve('/sessions')}>Browse race replays</a>
	</p>
	<p class="muted">Version {brand.version}.</p>
</article>

<style>
	.numbers {
		margin-top: var(--space-6);
	}
	.numbers h2 {
		margin-bottom: var(--space-1);
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
		gap: var(--space-3);
		margin-top: var(--space-3);
	}
	.tile {
		padding: var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
	}
	.value {
		font: 500 2.25rem / 1.05 var(--font-display);
		letter-spacing: -0.02em;
	}
	.what {
		margin-top: var(--space-2);
		font-weight: 600;
	}
	.from {
		margin-top: var(--space-1);
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.about h2 {
		font-family: var(--font-display);
		font-size: var(--font-size-lg);
		margin: var(--space-6) 0 var(--space-2);
	}

	.about ul {
		padding-left: var(--space-5);
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-4);
		margin-top: var(--space-6);
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
</style>
