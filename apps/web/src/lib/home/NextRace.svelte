<!--
  The home page's next-race block (ROADMAP W3, mockup A): the next Grand
  Prix, a countdown to its next session, the weekend's sessions at the track
  and in the viewer's own time, the circuit drawn from its real outline, and
  the last race behind the spoiler shield (a link to watch it, never its
  result). Rendered on the server; times in the viewer's zone are added in
  the browser, where that zone is known.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { serverNowMs } from '$lib/delay-sync';
	import type { SeoSession } from '$lib/seo';
	import type { Point } from '$lib/track-map/types';
	import { Countdown, TrackThumbnail, sessionTime } from '$lib/ui';
	import {
		countdownLabel,
		nextSession,
		sessionPhase,
		trackTime,
		weekendDates,
		type ScheduleEvent
	} from './next-race';

	interface Props {
		event: ScheduleEvent;
		outline: Point[] | null;
		/** A session of this weekend running now, if any. */
		live: SeoSession | null;
		/** The latest completed race, for "watch from the start". */
		lastRace: SeoSession | null;
	}

	let { event, outline, live, lastRace }: Props = $props();

	let nowMs = $state(Date.now());
	let inBrowser = $state(false);
	onMount(() => {
		inBrowser = true;
		nowMs = serverNowMs();
		const timer = setInterval(() => (nowMs = serverNowMs()), 30_000);
		return () => clearInterval(timer);
	});

	let upcoming = $derived(nextSession(event, nowMs));
	let thisWeekend = $derived(
		event.sessions.length > 0 && new Date(event.sessions[0]!.start_utc).getTime() <= nowMs
	);
	let kicker = $derived(live ? 'Live now' : thisWeekend ? 'This weekend' : 'Next race');
	let venue = $derived(event.circuit_name ?? event.location);
</script>

<section class="next-race" aria-labelledby="next-race-title">
	<div class="text">
		<p class="kicker" class:live={live !== null}>
			{#if live}<span class="dot" aria-hidden="true"></span>{/if}{kicker}
		</p>
		<h2 id="next-race-title">{event.event_name}</h2>
		<p class="where">{venue} · {weekendDates(event)}</p>

		{#if live}
			<a class="primary" href={resolve('/live')}>Open the live race desk</a>
		{:else if upcoming}
			<div class="countdown">
				<Countdown startUtc={upcoming.start_utc} label={countdownLabel(upcoming)} />
			</div>
		{/if}

		<ol class="sessions" aria-label="Sessions this weekend">
			{#each event.sessions as session (session.start_utc)}
				{@const phase = sessionPhase(session, event, nowMs)}
				<li class={phase}>
					<span class="name">{session.name}</span>
					<span class="at num">{trackTime(session)} <span class="muted">at the track</span></span>
					<span class="yours num"
						>{#if inBrowser}{sessionTime(session.start_utc)}
							<span class="muted">your time</span>{:else}&nbsp;{/if}</span
					>
				</li>
			{/each}
		</ol>

		{#if !live}
			<p class="actions">
				<a class="primary" href={resolve('/live')}>Open the race desk</a>
				<a class="secondary" href={resolve('/about')}>How it stays in step with your TV</a>
			</p>
		{/if}
	</div>

	<div class="track">
		{#if outline}
			<TrackThumbnail points={outline} label={`${venue} outline`} draw width={520} height={360} />
		{/if}
	</div>
</section>

{#if lastRace}
	<section class="last-race" aria-labelledby="last-race-title">
		<div>
			<p class="kicker">Last race</p>
			<h2 id="last-race-title">
				{lastRace.eventName ?? `Round ${lastRace.round}`}{#if lastRace.circuitName}<span
						class="venue">, {lastRace.circuitName}</span
					>{/if}
			</h2>
			<p class="muted">The result stays hidden until you have watched it.</p>
		</div>
		<a class="primary" href={resolve(`/sessions/${lastRace.year}/${lastRace.round}`)}
			>Watch from the start</a
		>
	</section>
{/if}

<style>
	.next-race {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-5);
		align-items: center;
		padding: var(--space-5) 0;
	}
	@media (min-width: 900px) {
		.next-race {
			grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
			gap: var(--space-7);
		}
	}
	.kicker {
		margin: 0;
		display: inline-flex;
		align-items: center;
		gap: 8px;
		font: 500 var(--font-size-xs) / 1 var(--font-mono);
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--accent);
	}
	.kicker.live {
		color: var(--flag-red);
	}
	.dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: var(--flag-red);
	}
	h2 {
		margin: var(--space-2) 0 0;
		font: 500 clamp(2.25rem, 1.6rem + 2.6vw, 3.75rem) / 1.04 var(--font-display);
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
	.sessions {
		list-style: none;
		margin: var(--space-5) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
	.sessions li {
		display: grid;
		grid-template-columns: minmax(8.5rem, 1fr) auto;
		gap: 2px var(--space-4);
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}
	@media (min-width: 640px) {
		.sessions li {
			grid-template-columns: minmax(9rem, 1fr) auto minmax(11rem, auto);
		}
	}
	.sessions li.done {
		color: var(--muted);
	}
	.sessions li.next .name {
		color: var(--accent);
		font-weight: 600;
	}
	.at,
	.yours {
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
		text-align: right;
		white-space: nowrap;
	}
	.yours {
		grid-column: 2;
	}
	@media (min-width: 640px) {
		.yours {
			grid-column: 3;
		}
	}
	.muted {
		color: var(--muted);
		font-family: var(--font-ui);
		font-size: var(--font-size-xs);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin-top: var(--space-5);
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
	.track {
		padding: var(--space-5);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		min-height: 200px;
	}
	.last-race {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
		flex-wrap: wrap;
		padding: var(--space-4) var(--space-5);
		border: 1px solid var(--rule-strong);
		border-radius: var(--radius-lg);
		background: var(--board);
		color: var(--board-ink);
	}
	.last-race h2 {
		margin: var(--space-1) 0 0;
		font-size: var(--font-size-xl);
	}
	.last-race .venue {
		color: var(--board-muted);
		font-style: italic;
	}
	.last-race .muted {
		color: var(--board-muted);
		margin: var(--space-1) 0 0;
	}
	.last-race .kicker {
		color: var(--board-muted);
	}
</style>
