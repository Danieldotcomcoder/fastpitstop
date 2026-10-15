<!--
  `/live` while no session is running (ROADMAP W2 D5): three screens, picked
  by `idleState`.
  - Off week: the next Grand Prix, then what is measured at its circuit.
  - Pre-session: a countdown to the next session as the hero, the weekend's
    earlier sessions to watch from the start, and how to get set.
  - Post-session: the session that just finished, offered from its start
    with the result hidden, then the next session.
  Nothing here names a result.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { serverNowMs } from '$lib/delay-sync';
	import NextRace from '$lib/home/NextRace.svelte';
	import type { NextRaceView } from '$lib/home/load';
	import { countdownLabel } from '$lib/home/next-race';
	import WeekendSessions from '$lib/home/WeekendSessions.svelte';
	import type { SeoSession } from '$lib/seo';
	import { Countdown, TrackThumbnail } from '$lib/ui';
	import BeforeWeekend from './BeforeWeekend.svelte';
	import type { CircuitBrief } from './circuit-brief';
	import { idleState, type EarlierSession, type HeldSession } from './idle';

	interface Props {
		next: NextRaceView;
		held: readonly HeldSession[];
		lastRace: SeoSession | null;
		circuit: CircuitBrief | null;
	}

	let { next, held, lastRace, circuit }: Props = $props();

	let nowMs = $state(Date.now());
	let inBrowser = $state(false);
	onMount(() => {
		inBrowser = true;
		nowMs = serverNowMs();
		const timer = setInterval(() => (nowMs = serverNowMs()), 15_000);
		return () => clearInterval(timer);
	});

	let idle = $derived(idleState(next.event, held, nowMs));
	let venue = $derived(next.event.circuit_name ?? next.event.location);
</script>

{#snippet earlierList(earlier: EarlierSession[])}
	{#if earlier.length > 0}
		<section class="panel" aria-labelledby="earlier-title" data-testid="earlier-sessions">
			<h2 id="earlier-title">Earlier this weekend</h2>
			<p class="muted">Results hidden until you watch them.</p>
			<ul class="earlier">
				{#each earlier as e (e.session.start_utc)}
					<li>
						<span>{e.session.name}</span>
						{#if e.href}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
							<a class="chip" href={e.href}>Watch from the start</a>
						{:else}
							<span class="muted">No replay yet</span>
						{/if}
					</li>
				{/each}
			</ul>
		</section>
	{/if}
{/snippet}

{#if idle.kind === 'off'}
	<div data-testid="live-off">
		<NextRace event={next.event} outline={next.outline} live={null} {lastRace} />
	</div>
{:else}
	<section class="hero" aria-labelledby="idle-title" data-testid="live-{idle.kind}">
		<div class="text">
			<p class="kicker">{next.event.event_name} · this weekend</p>
			{#if idle.kind === 'pre'}
				<h1 id="idle-title">{idle.next.name}</h1>
				<div class="big-count">
					<Countdown startUtc={idle.next.start_utc} label={countdownLabel(idle.next)} />
				</div>
				<div class="sessions">
					<WeekendSessions event={next.event} {nowMs} {inBrowser} />
				</div>
			{:else}
				<h1 id="idle-title">{idle.finished.name} has finished</h1>
				<p class="lede">The result stays hidden until you have watched it.</p>
				<div class="shield">
					<strong>Watch it from the start</strong>
					<span
						>A replay from the beginning, at your speed, with nothing past your clock shown.</span
					>
					{#if idle.href}
						<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
						<a class="primary" href={idle.href}>Watch {idle.finished.name.toLowerCase()}</a>
					{:else}
						<span class="muted"
							>No replay of it yet: it appears here once the session is filed.</span
						>
					{/if}
				</div>
			{/if}
		</div>
		<div class="track">
			{#if next.outline}
				<TrackThumbnail points={next.outline} label={`${venue} outline`} width={520} height={360} />
			{/if}
		</div>
	</section>

	<div class="columns">
		{#if idle.kind === 'post' && idle.next}
			<section class="panel" aria-labelledby="next-title">
				<h2 id="next-title">Next: {idle.next.name}</h2>
				<Countdown startUtc={idle.next.start_utc} label={countdownLabel(idle.next)} />
			</section>
		{/if}
		{@render earlierList(idle.earlier)}
		{#if idle.kind === 'pre'}
			<section class="panel" aria-labelledby="getset-title">
				<h2 id="getset-title">Get set before it starts</h2>
				<ol class="getset">
					<li>
						<strong>Match your TV.</strong> On the desk, tap once when the lights go out on your screen:
						everything then waits for your picture.
					</li>
					<li>
						<strong>Keep the screen on.</strong> The desk can hold your phone awake for the session.
					</li>
					<li>
						<strong>Put it on your home screen.</strong> It opens like an app, one tap from the race.
					</li>
				</ol>
			</section>
		{/if}
	</div>
{/if}

{#if circuit}
	<BeforeWeekend {circuit} />
{/if}

<style>
	.hero {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-5);
		align-items: center;
		padding: var(--space-5) 0;
	}
	@media (min-width: 900px) {
		.hero {
			grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
			gap: var(--space-7);
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
		font: 500 clamp(2.25rem, 1.6rem + 2.6vw, 3.75rem) / 1.04 var(--font-display);
		letter-spacing: var(--tracking-display);
	}
	.big-count {
		margin-top: var(--space-4);
	}
	.sessions {
		margin-top: var(--space-5);
	}
	.big-count :global(.time) {
		font: 500 clamp(3rem, 2rem + 4vw, 5.5rem) / 1 var(--font-display);
		letter-spacing: -0.02em;
	}
	.lede {
		margin: var(--space-2) 0 0;
		color: var(--ink-2);
		font-size: var(--font-size-md);
	}
	.muted {
		color: var(--muted);
		font-size: var(--font-size-sm);
	}
	.shield {
		margin-top: var(--space-5);
		padding: var(--space-4) var(--space-5);
		border-radius: var(--radius-lg);
		background: var(--board);
		color: var(--board-ink);
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		align-items: flex-start;
	}
	.shield span {
		color: var(--board-muted);
		font-size: var(--font-size-sm);
	}
	.primary {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		padding: 0 var(--space-4);
		border-radius: var(--radius-md);
		background: var(--accent);
		color: var(--accent-ink);
		text-decoration: none;
		font-weight: 600;
		margin-top: var(--space-2);
	}
	.track {
		padding: var(--space-5);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
	}
	.columns {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
		gap: var(--space-4);
	}
	.columns:empty {
		display: none;
	}
	.panel {
		padding: var(--space-4) var(--space-5);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
	}
	.panel h2 {
		margin: 0 0 var(--space-2);
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}
	.earlier {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
	}
	.earlier li {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}
	.chip {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		padding: 0 var(--space-3);
		border: 1px solid var(--rule-strong);
		border-radius: 999px;
		color: var(--ink);
		text-decoration: none;
		font-size: var(--font-size-sm);
	}
	.getset {
		margin: var(--space-2) 0 0;
		padding-left: 1.25rem;
		display: grid;
		gap: var(--space-2);
		color: var(--ink-2);
	}
	.getset strong {
		color: var(--ink);
	}
</style>
