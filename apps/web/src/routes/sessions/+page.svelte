<script lang="ts">
	/** The archive by weekend (ROADMAP W2, mockup "Sessions archive"): the
	 * season as a strip of its circuits, then one card per round with every
	 * session it holds, each replayed from its start. Nothing here gives a
	 * result away. */
	import { onMount, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import { calendar, calendarSummary, weekends } from '$lib/archive/weekends';
	import Panel from '$lib/components/Panel.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { serverNowMs } from '$lib/delay-sync';
	import { seoFor } from '$lib/seo';
	import { TrackThumbnail } from '$lib/ui';
	import type { PageProps } from './$types';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	let { data }: PageProps = $props();

	// Rendered on the server with the list already in hand (see
	// `+page.server.ts`); the client fetch is the fallback when it is not.
	const initial = untrack(() => data.sessions) as SessionSummary[];
	let status = $state<'loading' | 'error' | 'ready'>(initial.length > 0 ? 'ready' : 'loading');
	let sessions = $state<SessionSummary[]>(initial);
	let errorMessage = $state('');
	let selectedYear = $state<number | null>(null);
	let nowMs = $state(Date.now());

	async function loadSessions(): Promise<void> {
		status = 'loading';
		try {
			const { data, error } = await api.GET('/sessions');
			if (error) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			sessions = data;
			status = 'ready';
		} catch {
			status = 'error';
			errorMessage = 'Could not reach the API — is it running?';
		}
	}

	let years = $derived([...new Set(sessions.map((s) => s.session_key.year))].sort((a, b) => b - a));
	let year = $derived(selectedYear ?? years[0] ?? null);
	let cards = $derived(year === null ? [] : weekends(sessions, year));
	let rounds = $derived(
		year === null ? [] : calendar(data.schedules?.[year] ?? [], sessions, nowMs)
	);

	onMount(() => {
		nowMs = serverNowMs();
		if (sessions.length === 0) void loadSessions();
	});
</script>

<svelte:head>
	<title>{seoFor('/sessions', []).title}</title>
</svelte:head>

<div class="archive wide-dashboard">
	<header class="head">
		<div>
			<h1>Sessions</h1>
			<p class="lede">
				Every session we hold, by weekend. Nothing here gives a result away: open one to replay it
				from the start.
			</p>
		</div>
		{#if years.length > 0}
			<div class="year-tabs" role="tablist" aria-label="Season">
				{#each years as y (y)}
					<button
						type="button"
						role="tab"
						class="year-tab"
						class:active={year === y}
						aria-selected={year === y}
						onclick={() => (selectedYear = y)}
					>
						{y}
					</button>
				{/each}
			</div>
		{/if}
	</header>

	{#if rounds.length > 0}
		<section class="season" aria-labelledby="season-title">
			<h2 id="season-title" class="caps">{year} · {calendarSummary(rounds)}</h2>
			<ol class="strip" data-testid="season-strip">
				{#each rounds as r (r.round)}
					{@const thumb = r.circuitId ? data.thumbsById?.[r.circuitId] : undefined}
					<li class={r.state} class:unheld={r.state === 'run' && !r.held}>
						<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
						<a href={r.href} title={r.eventName} aria-label={`Round ${r.round}, ${r.eventName}`}>
							<span class="frame">
								{#if thumb}
									<TrackThumbnail
										fitted={thumb}
										label={`${r.circuitName ?? r.eventName} outline`}
										width={data.thumbSize.width}
										height={data.thumbSize.height}
										strokeWidth={data.thumbSize.stroke}
									/>
								{/if}
							</span>
							<span class="caps"
								>R{r.round}{r.state === 'next'
									? ' · next'
									: r.state === 'run' && !r.held
										? ' · no replay'
										: ''}</span
							>
						</a>
					</li>
				{/each}
			</ol>
		</section>
	{/if}

	{#if status === 'loading'}
		<LoadingState label="Loading sessions…" />
	{:else if status === 'error'}
		<Panel title="Couldn't load sessions">
			<p>{errorMessage}</p>
			<p class="hint">
				Start the API, then
				<button type="button" class="retry" onclick={loadSessions}>try again</button>.
			</p>
		</Panel>
	{:else if cards.length === 0}
		<Panel title="Nothing this season yet">
			<p>No session of this season is held yet. Pick another season.</p>
		</Panel>
	{:else}
		<ul class="weekends" aria-label="Weekends">
			{#each cards as w (w.round)}
				{@const thumb = w.circuitName ? data.cards?.[w.circuitName] : undefined}
				<li class="session-card" data-testid="weekend-card">
					<span class="thumb">
						{#if thumb}
							<TrackThumbnail
								fitted={thumb}
								label={`${w.circuitName} outline`}
								width={data.cardSize.width}
								height={data.cardSize.height}
								strokeWidth={data.cardSize.stroke}
							/>
						{/if}
					</span>
					<div class="body">
						<span class="caps"
							>Round {w.round}{#if w.liveRecorded}&nbsp;· taken in live{/if}{#if w.hasTelemetry}&nbsp;·
								telemetry{/if}{#if w.hasRadio}&nbsp;· radio{/if}</span
						>
						<h2 class="event">
							<a href={resolve(`/sessions/${w.year}/${w.round}`)}>{w.eventName}</a>
						</h2>
						{#if w.circuitName}<span class="meta">{w.circuitName}</span>{/if}
						<ul class="chips" aria-label={`${w.eventName} sessions`}>
							{#each w.sessions as session (session.type)}
								<li>
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
									<a class="chip" class:race={session.type === 'race'} href={session.href}
										>{session.label}{#if session.live}&nbsp;· live now{/if}</a
									>
								</li>
							{/each}
						</ul>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.archive {
		width: 100%;
		max-width: 1180px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}
	.head {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		gap: var(--space-4);
		flex-wrap: wrap;
	}
	h1 {
		margin: 0;
	}
	.lede {
		margin: var(--space-2) 0 0;
		color: var(--text-secondary);
		max-width: 60ch;
	}
	.caps {
		margin: 0;
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.year-tabs {
		display: flex;
		gap: var(--space-1);
		flex-wrap: wrap;
	}
	.year-tab {
		min-height: 44px;
		background: transparent;
		border: 1px solid var(--hairline);
		color: var(--text-muted);
		padding: 0 var(--space-4);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		font-size: var(--font-size-sm);
		border-radius: 999px;
		cursor: pointer;
	}
	.year-tab.active {
		color: var(--paper);
		border-color: var(--ink);
		background: var(--ink);
	}
	.strip {
		list-style: none;
		margin: var(--space-3) 0 0;
		padding: 0 0 var(--space-2);
		display: flex;
		gap: var(--space-2);
		overflow-x: auto;
	}
	.strip a {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		min-width: 88px;
		text-decoration: none;
	}
	.frame {
		display: block;
		width: 88px;
		height: 62px;
		padding: 5px 7px;
		box-sizing: border-box;
		border: 1px solid var(--rule);
		border-radius: var(--radius-md);
		background: var(--panel);
	}
	.strip li.next .frame {
		border-color: var(--warn-fill);
		box-shadow: 0 0 0 1px var(--warn-fill);
	}
	.strip li.next .caps {
		color: var(--ink);
	}
	/* Later rounds are only a promise: drawn faint, never as a held replay. */
	.strip li.later .frame {
		opacity: 0.45;
	}
	/* Run, but no replay held: the frame says so, not only the label. */
	.strip li.unheld .frame {
		border-style: dashed;
		opacity: 0.6;
	}
	.hint {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}
	.retry {
		background: none;
		border: none;
		padding: 0;
		color: var(--text-primary);
		text-decoration: underline;
		cursor: pointer;
	}
	.weekends {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: var(--space-3);
	}
	.session-card {
		display: grid;
		grid-template-columns: 132px minmax(0, 1fr);
		gap: var(--space-5);
		align-items: center;
		padding: var(--space-4) var(--space-5);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-lg);
		background: var(--surface-raised);
	}
	@media (max-width: 599px) {
		.session-card {
			grid-template-columns: 72px minmax(0, 1fr);
			gap: var(--space-3);
			padding: var(--space-3) var(--space-4);
		}
	}
	.body {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
	}
	.event {
		margin: 0;
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
		letter-spacing: var(--tracking-display);
	}
	.event a {
		color: inherit;
		text-decoration: none;
	}
	.event a:hover {
		text-decoration: underline;
	}
	.meta {
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}
	.chips {
		list-style: none;
		margin: var(--space-2) 0 0;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
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
	.chip:hover {
		background: var(--panel-sunk);
	}
	.chip.race {
		border-color: var(--accent);
	}
</style>
