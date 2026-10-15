<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ConfidenceBadge from '$lib/components/ConfidenceBadge.svelte';
	import { serverNowMs } from '$lib/delay-sync';
	import { countdownLabel, nextSession, weekendDates } from '$lib/home/next-race';
	import WeekendSessions from '$lib/home/WeekendSessions.svelte';
	import { planLabel, raceSlug, strategySentence } from '$lib/races/view';
	import { seoFor } from '$lib/seo';
	import { Countdown, TrackThumbnail } from '$lib/ui';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let race = $derived(data.race);
	let title = $derived(seoFor(page.url.pathname, [], [], [data.seo]).title);
	let venue = $derived(data.circuit?.name ?? race.circuit_name ?? race.location);

	let nowMs = $state(Date.now());
	let inBrowser = $state(false);
	onMount(() => {
		inBrowser = true;
		nowMs = serverNowMs();
		const timer = setInterval(() => (nowMs = serverNowMs()), 30_000);
		return () => clearInterval(timer);
	});

	let upcoming = $derived(nextSession(race, nowMs));
	let topPlans = $derived(data.previous?.strategy.plans.slice(0, 5) ?? []);
	let widestPlan = $derived(Math.max(1, ...topPlans.map((p) => p.cars)));
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<article class="race wide-dashboard">
	<section class="hero" aria-labelledby="race-title">
		<div class="text">
			<p class="kicker">Round {race.round} · {race.season}</p>
			<h1 id="race-title">{race.event_name}</h1>
			<p class="where">{venue}, {race.location} · {weekendDates(race)}</p>

			{#if data.live}
				<p class="state live"><span class="dot" aria-hidden="true"></span>On track now</p>
				<p class="actions">
					<a class="primary" href={resolve('/live')}>Open the live race desk</a>
				</p>
			{:else if upcoming}
				<div class="countdown">
					<Countdown startUtc={upcoming.start_utc} label={countdownLabel(upcoming)} />
				</div>
				<p class="actions">
					<a class="primary" href={resolve('/live')}>Open the race desk</a>
					<a
						class="secondary"
						href={resolve('/races/[year]/[slug]/calendar.ics', {
							year: String(race.season),
							slug: raceSlug(race.event_name)
						})}
						download>Add to calendar</a
					>
				</p>
			{:else}
				<p class="state">The weekend is over.</p>
				<p class="actions">
					{#if data.replay}
						<a
							class="primary"
							href={resolve('/sessions/[year]/[round]', {
								year: String(race.season),
								round: String(race.round)
							})}>Watch the replay, spoiler-free</a
						>
					{:else}
						<span class="muted">The replay opens here once the race is archived.</span>
					{/if}
				</p>
			{/if}
		</div>
		<div class="card map">
			{#if data.circuit?.outline}
				<TrackThumbnail
					fitted={data.circuit.outline}
					label={`${venue} outline`}
					draw
					width={data.mapSize.width}
					height={data.mapSize.height}
				/>
			{/if}
		</div>
	</section>

	<div class="cards">
		<section class="card" aria-labelledby="weekend-heading">
			<h2 id="weekend-heading">The weekend</h2>
			<div class="sessions">
				<WeekendSessions event={race} {nowMs} {inBrowser} />
			</div>
		</section>

		{#if data.circuit}
			<section class="card" aria-labelledby="stop-heading">
				<h2 id="stop-heading">What a stop costs here</h2>
				<p class="big num">
					{data.circuit.pitLoss.value}
					<ConfidenceBadge level={data.circuit.pitLoss.tier} />
				</p>
				<p class="note">
					{data.circuit.pitLoss.detail}.
					{#if data.circuit.range}
						Across the calendar a stop costs {data.circuit.range.low.toFixed(1)} to
						{data.circuit.range.high.toFixed(1)} s.
					{/if}
				</p>
				<p class="facts num">
					<span>{data.circuit.lapKm.toFixed(3)} km</span>
					{#if data.circuit.corners}<span>{data.circuit.corners} corners</span>{/if}
					{#if data.circuit.drsZones}<span
							>{data.circuit.drsZones} DRS zone{data.circuit.drsZones === 1 ? '' : 's'}</span
						>{/if}
				</p>
				<a class="more" href={resolve('/circuits/[slug]', { slug: data.circuit.slug })}
					>Tyre wear and the corners at {data.circuit.name} →</a
				>
			</section>
		{/if}

		{#if data.previous}
			<section class="card" aria-labelledby="plan-heading">
				<h2 id="plan-heading">How {data.previous.season} was run</h2>
				<p class="lead">{strategySentence(data.previous.strategy)} Their tyre plans:</p>
				<ul class="plans">
					{#each topPlans as plan (plan.compounds.join('-'))}
						<li>
							<span>{planLabel(plan.compounds)}</span>
							<span class="bar" aria-hidden="true"
								><span style="width: {((plan.cars / widestPlan) * 100).toFixed(0)}%"></span></span
							>
							<span class="num">{plan.cars}</span>
						</li>
					{/each}
				</ul>
				<p class="note">Strategy only: no results on this page.</p>
			</section>
		{/if}
	</div>
</article>

<style>
	.race {
		width: 100%;
		max-width: 1180px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}

	.hero {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-5);
		align-items: center;
		padding: var(--space-4) 0 0;
	}

	@media (min-width: 900px) {
		.hero {
			grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
			gap: var(--space-6);
		}
	}

	.kicker {
		margin: 0;
		font: 500 var(--font-size-xs) / 1 var(--font-mono);
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--accent);
	}

	h1 {
		margin: var(--space-2) 0 0;
		font: 500 clamp(2.25rem, 1.6rem + 2.6vw, 3.25rem) / 1.04 var(--font-display);
		letter-spacing: var(--tracking-display);
	}

	.where {
		margin: var(--space-2) 0 0;
		color: var(--ink-2);
		font-size: var(--font-size-md);
	}

	.countdown {
		margin-top: var(--space-5);
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		display: inline-block;
	}

	.state {
		margin: var(--space-5) 0 0;
		display: flex;
		align-items: center;
		gap: 8px;
		font-weight: 600;
	}

	.state.live {
		color: var(--flag-red);
	}

	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--flag-red);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-3);
		margin: var(--space-4) 0 0;
	}

	.primary,
	.secondary {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		padding: 0 var(--space-4);
		border-radius: var(--radius-md);
		text-decoration: none;
		font-weight: 600;
	}

	.primary {
		background: var(--accent);
		color: var(--accent-ink);
	}

	.secondary {
		border: 1px solid var(--rule-strong);
		color: var(--ink);
		font-weight: 500;
	}

	.card {
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		box-shadow: var(--elev-1);
		padding: var(--space-4);
	}

	.map {
		padding: var(--space-4) var(--space-5);
		min-height: 160px;
	}

	.cards {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-4);
		align-items: start;
	}

	@media (min-width: 900px) {
		.cards {
			grid-template-columns: repeat(3, minmax(0, 1fr));
		}
	}

	h2 {
		margin: 0;
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}

	.sessions {
		margin-top: var(--space-2);
	}

	/* Three columns are too narrow for the track and viewer times side by
	   side: stack them under the session name. */
	@media (min-width: 900px) {
		.sessions :global(li) {
			grid-template-columns: minmax(0, 1fr) auto;
		}
		.sessions :global(.yours) {
			grid-column: 2;
		}
	}

	.big {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin: var(--space-2) 0 0;
		font: 500 var(--num-xl) / 1.1 var(--font-display);
	}

	.note {
		margin: var(--space-2) 0 0;
		font-size: var(--font-size-sm);
		color: var(--muted);
	}

	.facts {
		display: flex;
		gap: var(--space-4);
		margin: var(--space-3) 0 0;
		font: 500 var(--font-size-sm) / 1 var(--font-mono);
		color: var(--ink-2);
	}

	.more {
		display: inline-block;
		margin-top: var(--space-3);
		font-size: var(--font-size-sm);
		color: var(--accent);
	}

	.lead {
		margin: var(--space-2) 0 var(--space-1);
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}

	.plans {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.plans li {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 6rem 2rem;
		gap: var(--space-3);
		align-items: center;
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
		font-size: var(--font-size-sm);
	}

	.plans .bar {
		display: block;
		height: 7px;
		border-radius: 4px;
		background: var(--panel-sunk);
	}

	.plans .bar span {
		display: block;
		height: 7px;
		border-radius: 4px;
		background: var(--accent);
	}

	.plans .num {
		text-align: right;
		font-family: var(--font-mono);
	}

	.muted {
		color: var(--muted);
		font-size: var(--font-size-sm);
	}
</style>
