<script lang="ts">
	/** The front page: what FastPitStop is, in text a search engine can read,
	 * with the live dashboard one tap away. Rendered on the server (see
	 * `+page.server.ts`). When a session is running, the first thing on the
	 * page is the way into it. Nothing here names a result. */
	import { resolve } from '$app/paths';
	import { brand } from '$lib/brand';
	import { HOME_FAQ, seoFor, type SeoSession } from '$lib/seo';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const SESSION_LABELS: Record<string, string> = {
		race: 'Race',
		sprint: 'Sprint',
		qualifying: 'Qualifying',
		sprint_qualifying: 'Sprint Qualifying',
		practice_1: 'Practice 1',
		practice_2: 'Practice 2',
		practice_3: 'Practice 3'
	};

	const title = seoFor('/').title;
	let live = $derived(data.sessions.find((s) => s.live) ?? null);

	/** The latest Grand Prix weekends, newest first, one link each. */
	let recent = $derived.by(() => {
		// A local lookup built and read inside this derivation, never
		// reactive state, so SvelteMap is not needed.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const rounds = new Map<string, SeoSession>();
		for (const s of data.sessions) {
			const key = `${s.year}/${s.round}`;
			const kept = rounds.get(key);
			if (!kept || s.sessionType === 'race') rounds.set(key, s);
		}
		return [...rounds.values()].sort((a, b) => b.year - a.year || b.round - a.round).slice(0, 8);
	});

	function eventName(s: SeoSession): string {
		return s.eventName ? `${s.year} ${s.eventName}` : `${s.year} Round ${s.round}`;
	}
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<article class="home">
	{#if live}
		<a class="live-now" href={resolve('/live')}>
			<span class="dot" aria-hidden="true"></span>
			<span class="label">Live now</span>
			<span class="what"
				>{eventName(live)} · {SESSION_LABELS[live.sessionType] ?? live.sessionType}</span
			>
			<span class="go">Open the live dashboard →</span>
		</a>
	{/if}

	<header class="hero">
		<p class="kicker">Unofficial F1 dashboard</p>
		<h1>F1 live timing that never spoils the race</h1>
		<p class="lede">
			{brand.name} is a free second-screen dashboard for Formula 1. It shows live timing, tyre wear, pit
			strategy and undercut predictions, and holds every number back to match your TV delay so you never
			see a moment before it happens on screen.
		</p>
		<p class="actions">
			<a class="primary" href={resolve('/live')}>Open the live dashboard</a>
			<a class="secondary" href={resolve('/sessions')}>Browse race replays</a>
		</p>
	</header>

	<section aria-labelledby="features">
		<h2 id="features">More than the timing screen</h2>
		<div class="features">
			<div>
				<h3>Live timing and gaps</h3>
				<p>
					Running order, intervals, last laps, tyre compound and age, pit stops and race control
					messages, updated as the session runs. Open it at any point and it shows where the race
					stands now.
				</p>
			</div>
			<div>
				<h3>Spoiler-free Delay Sync</h3>
				<p>
					TV and streaming feeds run seconds to a minute behind the track. Set your delay, or tap
					when the lights go out, and everything waits for your broadcast, even if your phone's
					clock is wrong.
				</p>
			</div>
			<div>
				<h3>Undercut and pit-window predictor</h3>
				<p>
					For the closest battles it predicts whether pitting now would get a driver ahead, and on
					which lap, with a confidence level. Tested against past races it never saw.
				</p>
			</div>
			<div>
				<h3>Tyre degradation</h3>
				<p>
					How fast each set of tyres is losing time, fitted per stint from fuel-corrected laps, with
					an honest verdict on how far each fit can be trusted.
				</p>
			</div>
			<div>
				<h3>Why it happened</h3>
				<p>
					When something notable happens, such as a pass, a slow stop or a sudden loss of pace, one
					sentence explains it, citing the timing and race control evidence behind it.
				</p>
			</div>
			<div>
				<h3>Race replays</h3>
				<p>
					Rewatch past Grands Prix lap by lap at any speed through the same dashboard, with the same
					spoiler protection and the cars on F1's own recorded positions.
				</p>
			</div>
		</div>
	</section>

	{#if recent.length > 0}
		<section aria-labelledby="recent">
			<h2 id="recent">Recent races</h2>
			<ul class="recent">
				{#each recent as s (`${s.year}/${s.round}`)}
					<li>
						<a href={resolve(`/sessions/${s.year}/${s.round}`)}>{eventName(s)}</a>
						{#if s.circuitName}<span class="venue">{s.circuitName}</span>{/if}
					</li>
				{/each}
			</ul>
			<p><a href={resolve('/sessions')}>All races →</a></p>
		</section>
	{/if}

	<section aria-labelledby="faq">
		<h2 id="faq">Questions</h2>
		<dl class="faq">
			{#each HOME_FAQ as item (item.question)}
				<dt>{item.question}</dt>
				<dd>{item.answer}</dd>
			{/each}
		</dl>
	</section>
</article>

<style>
	.home {
		display: flex;
		flex-direction: column;
		gap: var(--space-7);
	}

	.live-now {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--accent);
		border-radius: var(--radius-md);
		background: var(--accent-wash);
		color: var(--text-primary);
		text-decoration: none;
	}

	.live-now .dot {
		width: 0.6rem;
		height: 0.6rem;
		border-radius: 50%;
		background: var(--flag-red, #d33);
	}

	.live-now .label {
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		font-size: var(--font-size-xs);
	}

	.live-now .go {
		margin-left: auto;
		color: var(--accent);
		font-weight: 600;
	}

	.hero h1 {
		font-family: var(--font-display);
		font-size: var(--font-size-2xl);
		line-height: 1.15;
		margin: var(--space-2) 0 var(--space-4);
	}

	.kicker {
		font-size: var(--font-size-xs);
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-muted);
		margin: 0;
	}

	.lede {
		font-size: var(--font-size-md);
		color: var(--text-secondary);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
	}

	.actions a {
		padding: var(--space-3) var(--space-4);
		border-radius: var(--radius-md);
		text-decoration: none;
		font-weight: 600;
	}

	.actions .primary {
		background: var(--accent);
		color: var(--accent-ink);
	}

	.actions .secondary {
		border: 1px solid var(--hairline-strong);
		color: var(--text-primary);
	}

	h2 {
		font-family: var(--font-display);
		font-size: var(--font-size-xl);
		margin: 0 0 var(--space-4);
	}

	.features {
		display: grid;
		gap: var(--space-5);
	}

	.features h3 {
		font-size: var(--font-size-md);
		margin: 0 0 var(--space-2);
	}

	.features p {
		margin: 0;
		color: var(--text-secondary);
	}

	.recent {
		list-style: none;
		padding: 0;
		margin: 0 0 var(--space-3);
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.recent li {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1) var(--space-3);
		align-items: baseline;
	}

	.venue {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}

	.faq dt {
		font-weight: 600;
		margin-top: var(--space-4);
	}

	.faq dd {
		margin: var(--space-1) 0 0;
		color: var(--text-secondary);
	}
</style>
