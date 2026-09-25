<script lang="ts">
	/** Race story — the salience-ranked narrative of explained moments for
	 * one session. Shareable / demo-worthy surface: REST-fetched anomalies
	 * (already salience-sorted by `f1_api`) with lazy-fetched grounded
	 * explanations, each expandable to the real cited sources. Mobile-first. */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import ExplanationCard from '$lib/components/ExplanationCard.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import {
		buildStoryMoments,
		explanationViewFromApi,
		type ExplanationView,
		type StoryMoment
	} from '$lib/why';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	let sessionsStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let storyStatus = $state<'idle' | 'loading' | 'error' | 'ready'>('idle');
	let sessions = $state<SessionSummary[]>([]);
	let selectedKey = $state<string | null>(null);
	let errorMessage = $state('');
	let moments = $state<StoryMoment[]>([]);
	let sessionSelectEl: HTMLSelectElement | undefined = $state();

	$effect(() => {
		void sessions.length;
		if (sessionSelectEl) sessionSelectEl.value = selectedKey ?? '';
	});

	async function loadSessions(): Promise<void> {
		sessionsStatus = 'loading';
		try {
			const { data, error } = await api.GET('/sessions');
			if (error) {
				sessionsStatus = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			sessions = data;
			sessionsStatus = 'ready';
		} catch {
			sessionsStatus = 'error';
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	/** One request, not one per moment.
	 *
	 * This used to list `/sessions/{key}/anomalies` and then fire a
	 * `/anomalies/{id}/explanation` per row in a `Promise.all`. Every one of
	 * those re-ran the whole `analyze_session` + `detect_anomalies` chain
	 * server-side — seconds each — and a real race detects a few hundred
	 * anomalies, so the page fired hundreds of concurrent multi-second
	 * requests and never finished. `GET /sessions/{key}/story` detects once
	 * and returns the salience-ranked, already-narrated moments. */
	async function loadStory(key: string): Promise<void> {
		selectedKey = key;
		storyStatus = 'loading';
		moments = [];
		try {
			const { data, error } = await api.GET('/sessions/{key}/story', {
				params: { path: { key } }
			});
			if (error || !data) {
				storyStatus = 'error';
				errorMessage = 'Could not load the story for this session.';
				return;
			}
			// A local lookup built once inside this load and then read;
			// never reactive state, so SvelteMap is not needed.
			// eslint-disable-next-line svelte/prefer-svelte-reactivity
			const explanationsById = new Map<string, ExplanationView>();
			for (const moment of data) {
				if (moment.explanation) {
					explanationsById.set(moment.anomaly.anomaly_id, explanationViewFromApi(moment.explanation));
				}
			}
			moments = buildStoryMoments(
				data.map((moment) => moment.anomaly),
				explanationsById
			);
			storyStatus = 'ready';
		} catch {
			storyStatus = 'error';
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	function syncUrl(key: string): void {
		void goto(resolve(`/story?key=${encodeURIComponent(key)}`), {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	}

	function onSessionChange(event: Event): void {
		const key = (event.currentTarget as HTMLSelectElement).value;
		if (!key) return;
		void loadStory(key);
		syncUrl(key);
	}

	onMount(() => {
		void loadSessions();
		const params = new URLSearchParams(window.location.search);
		const initialKey = params.get('key');
		if (initialKey) void loadStory(initialKey);
	});
</script>

<h1>Race story</h1>
<p>
	The salience-ranked narrative of why this race unfolded as it did — every moment grounded in
	retrieved evidence, with honest “no clear cause” when the sources are thin.
</p>

{#if sessionsStatus === 'loading'}
	<LoadingState label="Loading sessions…" />
{:else if sessionsStatus === 'error'}
	<Panel title="Couldn't load sessions">
		<p>{errorMessage}</p>
	</Panel>
{:else}
	<Panel title="Session">
		<label class="picker-field">
			<span>Session</span>
			<select
				bind:this={sessionSelectEl}
				data-testid="story-session-select"
				value={selectedKey ?? ''}
				onchange={onSessionChange}
			>
				<option value="" disabled>Choose a session…</option>
				{#each sessions as session (session.key)}
					<option value={session.key}>{session.event_name ?? session.key}</option>
				{/each}
			</select>
		</label>
	</Panel>
{/if}

{#if selectedKey}
	{#if storyStatus === 'loading'}
		<LoadingState label="Building the race story…" />
	{:else if storyStatus === 'error'}
		<Panel title="Couldn't load story">
			<p>{errorMessage}</p>
		</Panel>
	{:else if moments.length === 0}
		<Panel title="No explained moments">
			<p>No anomalies crossed the salience bar for this session.</p>
		</Panel>
	{:else}
		<ol class="story" data-testid="race-story">
			{#each moments as moment, index (moment.anomalyId)}
				<li class="story-item">
					<span class="rank font-mono" aria-hidden="true">{index + 1}</span>
					{#if moment.explanation}
						<ExplanationCard explanation={moment.explanation} salience={moment.salience} />
					{:else}
						<article class="pending" data-testid="explanation-card" data-grounded="false">
							<p>Explanation unavailable for this moment.</p>
						</article>
					{/if}
				</li>
			{/each}
		</ol>
	{/if}
{/if}

<style>
	.picker-field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
		max-width: 28rem;
	}

	select {
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
	}

	.story {
		list-style: none;
		margin: var(--space-5) 0 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		counter-reset: none;
	}

	.story-item {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: var(--space-3);
		align-items: start;
		animation: story-in 360ms var(--ease-standard) both;
		animation-delay: calc(min(var(--i, 0), 8) * 40ms);
	}

	.story-item:nth-child(1) {
		--i: 0;
	}
	.story-item:nth-child(2) {
		--i: 1;
	}
	.story-item:nth-child(3) {
		--i: 2;
	}
	.story-item:nth-child(4) {
		--i: 3;
	}
	.story-item:nth-child(5) {
		--i: 4;
	}
	.story-item:nth-child(6) {
		--i: 5;
	}
	.story-item:nth-child(7) {
		--i: 6;
	}
	.story-item:nth-child(8) {
		--i: 7;
	}

	.rank {
		width: 1.75rem;
		height: 1.75rem;
		display: grid;
		place-items: center;
		border-radius: 50%;
		border: 1px solid var(--color-border-strong);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
		margin-top: var(--space-3);
	}

	.pending {
		padding: var(--space-3);
		border: 1px dashed var(--color-border);
		border-radius: var(--radius-md);
		color: var(--color-text-muted);
	}

	@keyframes story-in {
		from {
			opacity: 0;
			transform: translateY(0.5rem);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.story-item {
			animation: none;
		}
	}
</style>
