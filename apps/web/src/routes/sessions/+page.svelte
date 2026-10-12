<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import Panel from '$lib/components/Panel.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { SESSION_TYPE_LABELS, seoFor } from '$lib/seo';
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
	/** Races only by default — practice/qualifying behind a chip. */
	let includeNonRace = $state(false);
	let selectedYear = $state<number | null>(null);

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

	$effect(() => {
		if (selectedYear === null && years.length > 0) {
			selectedYear = years[0] ?? null;
		}
	});

	let filtered = $derived.by(() => {
		return sessions
			.filter((s) => {
				if (selectedYear !== null && s.session_key.year !== selectedYear) return false;
				if (!includeNonRace && s.session_key.session_type !== 'race') return false;
				return true;
			})
			.sort((a, b) => {
				if (a.session_key.year !== b.session_key.year) {
					return b.session_key.year - a.session_key.year;
				}
				return b.session_key.round - a.session_key.round;
			});
	});

	/** The circuit's outline, fitted on the server (none when the page
	 * fetched its list itself). */
	function thumbFor(session: SessionSummary): string | undefined {
		return session.circuit_name ? data.thumbs?.[session.circuit_name] : undefined;
	}

	function sessionHref(session: SessionSummary): string {
		const { year, round, session_type } = session.session_key;
		const path = resolve(`/sessions/${year}/${round}`);
		return session_type === 'race' ? path : `${path}?session=${session_type}`;
	}

	onMount(() => {
		if (sessions.length === 0) void loadSessions();
	});
</script>

<svelte:head>
	<title>{seoFor('/sessions', []).title}</title>
</svelte:head>

<h1>Sessions</h1>
<p>
	Completed races, newest first. Nothing here gives a result away: open one to replay it from the
	start.
</p>

<div class="toolbar">
	{#if years.length > 0}
		<div class="year-tabs" role="tablist" aria-label="Season">
			{#each years as year (year)}
				<button
					type="button"
					role="tab"
					class="year-tab"
					class:active={selectedYear === year}
					aria-selected={selectedYear === year}
					onclick={() => (selectedYear = year)}
				>
					{year}
				</button>
			{/each}
		</div>
	{/if}
	<label class="chip">
		<input type="checkbox" bind:checked={includeNonRace} />
		Include qualifying &amp; practice
	</label>
</div>

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
{:else if filtered.length === 0}
	<Panel title="Nothing in this filter">
		<p>
			No sessions match the current year and type filter. Turn on qualifying &amp; practice, or pick
			another season.
		</p>
	</Panel>
{:else}
	<ul class="session-list">
		{#each filtered as session (session.key)}
			{@const thumb = thumbFor(session)}
			<li>
				<!-- sessionHref() calls resolve() itself (see above). -->
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
				<a class="session-card" href={sessionHref(session)}>
					<span class="round tabular">R{session.session_key.round}</span>
					<span class="body">
						<span class="event">{session.event_name ?? session.key}</span>
						<span class="meta">
							{#if session.circuit_name}
								{session.circuit_name}
								<span class="sep" aria-hidden="true">·</span>
							{/if}
							{SESSION_TYPE_LABELS[session.session_key.session_type] ??
								session.session_key.session_type}
						</span>
						<span class="badges">
							{#if session.has_radio}
								<span class="badge">radio</span>
							{/if}
							{#if session.has_telemetry}
								<span class="badge">telemetry</span>
							{/if}
							{#if session.live_recorded}
								<span class="badge">live-recorded</span>
							{/if}
							{#if !session.has_radio && !session.has_telemetry && !session.live_recorded}
								<span class="badge muted">timing</span>
							{/if}
						</span>
					</span>
					{#if thumb}
						<span class="thumb">
							<TrackThumbnail
								fitted={thumb}
								label={`${session.circuit_name} outline`}
								width={data.thumbSize.width}
								height={data.thumbSize.height}
								strokeWidth={data.thumbSize.stroke}
							/>
						</span>
					{/if}
				</a>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		margin-bottom: var(--space-5);
	}

	.year-tabs {
		display: flex;
		gap: var(--space-1);
		flex-wrap: wrap;
	}

	.year-tab {
		background: transparent;
		border: 1px solid var(--hairline);
		color: var(--text-muted);
		padding: var(--space-1) var(--space-3);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		font-size: var(--font-size-sm);
		border-radius: var(--radius-sm);
		cursor: pointer;
	}

	.year-tab.active {
		color: var(--text-primary);
		border-color: var(--hairline-strong);
		background: var(--surface-raised);
	}

	.chip {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--font-size-sm);
		color: var(--text-secondary);
		cursor: pointer;
		user-select: none;
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

	.session-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.session-card {
		display: flex;
		gap: var(--space-4);
		align-items: flex-start;
		padding: var(--space-4);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-md);
		background: var(--surface-raised);
		text-decoration: none;
		color: inherit;
		transition:
			border-color var(--duration-fast) ease,
			background var(--duration-fast) ease;
	}

	.session-card:hover {
		border-color: var(--hairline-strong);
		background: var(--surface-overlay);
		opacity: 1;
	}

	.session-card:focus-visible {
		border-color: var(--hairline-strong);
	}

	.thumb {
		flex: 0 0 72px;
		align-self: center;
	}

	.round {
		font-family: var(--font-display);
		font-weight: 600;
		letter-spacing: var(--tracking-display);
		color: var(--text-muted);
		min-width: 2.5rem;
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-width: 0;
		flex: 1;
	}

	.event {
		font-family: var(--font-display);
		font-size: var(--font-size-md);
		font-weight: 600;
		letter-spacing: var(--tracking-display);
		color: var(--text-primary);
	}

	.meta {
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.sep {
		margin: 0 0.25rem;
		opacity: 0.6;
	}

	.badges {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		margin-top: var(--space-1);
	}

	.badge {
		font-size: var(--font-size-xs);
		font-weight: 500;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--text-secondary);
		border: 1px solid var(--hairline);
		border-radius: var(--radius-sm);
		padding: 0.1rem 0.4rem;
	}

	.badge.muted {
		color: var(--text-muted);
	}

	.tabular {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
</style>
