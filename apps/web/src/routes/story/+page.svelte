<script lang="ts">
	/** The race story (ROADMAP W2, mockup "Story"): every explained moment
	 * of one session, each expandable to its cited sources, held back to the
	 * lap the viewer says they have watched. The heading says how far it is
	 * held; the race's own length is never shown, since a short race would
	 * give itself away. The chosen lap is remembered per session on the
	 * device, and `?upto=` (a lap, or `all`) sets it from a link. */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import ExplanationCard from '$lib/components/ExplanationCard.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import { holdStory, momentLap, storedUpTo, storeUpTo } from '$lib/why/story-view';
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
	/** The last lap watched; null for the whole race. Until the viewer says,
	 * nothing past the start shows. */
	let upTo = $state<number | null>(0);
	let held = $derived(holdStory(moments, upTo));
	let session = $derived(sessions.find((s) => s.key === selectedKey) ?? null);
	let eventName = $derived(session?.event_name ?? 'this race');

	function setUpTo(next: number | null): void {
		upTo = next === null ? null : Math.max(0, Math.round(next));
		if (selectedKey) storeUpTo(selectedKey, upTo);
	}

	/** The axis ends at the viewer's lap, followed by a held-back stretch of
	 * fixed size: it says "more to come", never how much. */
	let axisEnd = $derived.by(() => {
		if (upTo !== null) return Math.max(upTo, 1);
		return Math.max(1, ...held.shown.map((m) => momentLap(m) ?? 0));
	});

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
	async function loadStory(key: string, linked?: number | null): Promise<void> {
		selectedKey = key;
		const remembered = storedUpTo(key);
		upTo = linked !== undefined ? linked : remembered === undefined ? 0 : remembered;
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
					explanationsById.set(
						moment.anomaly.anomaly_id,
						explanationViewFromApi(moment.explanation)
					);
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
		const raw = params.get('upto');
		const linked =
			raw === 'all' ? null : raw !== null && Number.isFinite(Number(raw)) ? Number(raw) : undefined;
		if (initialKey) void loadStory(initialKey, linked);
	});
</script>

<div class="story-page">
	<header>
		{#if session}
			<p class="kicker">
				{session.session_key.year} · Round {session.session_key.round} · {session.session_key.session_type.replace(
					'_',
					' '
				)}
			</p>
		{/if}
		<h1 data-testid="story-heading">
			{#if !selectedKey}The race story{:else if upTo === null}The story of the {eventName}{:else if upTo === 0}The
				story of the {eventName}, held back to the start{:else}The story of the {eventName}, to lap
				{upTo}{/if}
		</h1>
		<p class="lede">
			{#if upTo === null}
				Every explained moment, each with the timing and race control evidence behind it.
			{:else}
				Held back to {upTo === 0 ? 'the start' : `lap ${upTo}`}, where you said you are. Later
				moments stay hidden until you move on.
			{/if}
		</p>
	</header>

	{#if sessionsStatus === 'loading'}
		<LoadingState label="Loading sessions…" />
	{:else if sessionsStatus === 'error'}
		<Panel title="Couldn't load sessions">
			<p>{errorMessage}</p>
		</Panel>
	{:else}
		<div class="controls">
			<label class="picker-field">
				<span>Race</span>
				<select
					bind:this={sessionSelectEl}
					data-testid="story-session-select"
					value={selectedKey ?? ''}
					onchange={onSessionChange}
				>
					<option value="" disabled>Choose a session…</option>
					{#each sessions as s (s.key)}
						<option value={s.key}>{s.event_name ?? s.key}</option>
					{/each}
				</select>
			</label>
			{#if selectedKey}
				<div class="upto" role="group" aria-label="How far you have watched">
					<label for="upto-lap">I have watched up to lap</label>
					<div class="stepper">
						<button
							type="button"
							aria-label="One lap back"
							disabled={upTo === 0}
							onclick={() => setUpTo((upTo ?? axisEnd) - 1)}>−</button
						>
						<input
							id="upto-lap"
							type="number"
							min="0"
							inputmode="numeric"
							value={upTo ?? ''}
							placeholder="all"
							data-testid="story-upto"
							onchange={(e) => {
								const v = (e.currentTarget as HTMLInputElement).value;
								setUpTo(v === '' ? null : Number(v));
							}}
						/>
						<button
							type="button"
							aria-label="One lap on"
							onclick={() => setUpTo(upTo === null ? null : upTo + 1)}>+</button
						>
					</div>
					<button
						type="button"
						class="all"
						aria-pressed={upTo === null}
						onclick={() => setUpTo(null)}>The whole race</button
					>
				</div>
			{/if}
		</div>
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
				<p>Nothing in this session crossed the bar for a story.</p>
			</Panel>
		{:else}
			<div
				class="axis"
				role="img"
				aria-label={upTo === null
					? 'Every moment on a lap axis'
					: `Moments up to lap ${upTo} on a lap axis, later laps held back`}
			>
				<div class="track" class:held-on={upTo !== null}>
					{#each held.shown as moment (moment.anomalyId)}
						{@const lap = momentLap(moment)}
						{#if lap !== null}
							<span class="mark" style:left="{(lap / axisEnd) * 100}%"
								><i></i><span class="lap">{lap}</span></span
							>
						{/if}
					{/each}
				</div>
				{#if upTo !== null}<span class="held">Later laps held back</span>{/if}
			</div>

			{#if held.shown.length === 0}
				<p class="none">
					Nothing to tell yet at {upTo === 0 ? 'the start' : `lap ${upTo}`}. Move the lap on as you
					watch, or choose the whole race.
				</p>
			{:else}
				<div class="story" data-testid="race-story">
					{#each held.groups as group (group.id)}
						<section aria-labelledby="phase-{group.id}">
							<h2 id="phase-{group.id}">{group.title}</h2>
							<ol>
								{#each group.moments as moment (moment.anomalyId)}
									<li>
										{#if moment.explanation}
											<ExplanationCard
												explanation={moment.explanation}
												salience={moment.salience}
											/>
										{:else}
											<article class="pending" data-testid="explanation-card" data-grounded="false">
												<p>Explanation unavailable for this moment.</p>
											</article>
										{/if}
									</li>
								{/each}
							</ol>
						</section>
					{/each}
				</div>
			{/if}
		{/if}
	{/if}
</div>

<style>
	.story-page {
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}
	.kicker {
		margin: 0;
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	h1 {
		margin: var(--space-2) 0 0;
	}
	.lede {
		margin: var(--space-2) 0 0;
		color: var(--text-secondary);
	}
	.controls {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-4);
		align-items: flex-end;
		padding: var(--space-4);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
	}
	.picker-field,
	.upto {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}
	.upto {
		flex-direction: row;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-3);
	}
	select,
	input {
		min-height: 44px;
		padding: 0 var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--rule-strong);
		background: var(--paper);
		color: var(--ink);
		font: inherit;
	}
	input {
		width: 4.5rem;
		text-align: center;
		font-family: var(--font-mono);
	}
	.stepper {
		display: flex;
		gap: var(--space-1);
	}
	.stepper button,
	.all {
		min-width: 44px;
		min-height: 44px;
		border-radius: var(--radius-md);
		border: 1px solid var(--rule-strong);
		background: var(--panel);
		color: var(--ink);
		font: inherit;
		cursor: pointer;
	}
	.all {
		padding: 0 var(--space-4);
	}
	.all[aria-pressed='true'] {
		background: var(--ink);
		color: var(--paper);
	}
	.axis {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-2) var(--space-3) var(--space-5);
	}
	.track {
		position: relative;
		flex: 1;
		height: 2px;
		background: var(--rule-strong);
	}
	.mark {
		position: absolute;
		top: -6px;
		transform: translateX(-50%);
		display: flex;
		flex-direction: column;
		align-items: center;
	}
	.mark i {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: var(--accent);
		border: 2px solid var(--paper);
		box-sizing: border-box;
	}
	.lap {
		margin-top: 4px;
		font: 500 var(--font-size-xs) / 1 var(--font-mono);
		color: var(--muted);
	}
	.held {
		flex: 0 0 auto;
		padding: var(--space-2) var(--space-3);
		border: 1px dashed var(--rule-strong);
		border-radius: var(--radius-md);
		background: var(--panel-sunk);
		font: 500 var(--font-size-xs) / 1.2 var(--font-mono);
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.none {
		color: var(--muted);
	}
	.story {
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
		max-width: 780px;
	}
	.story h2 {
		margin: 0 0 var(--space-3);
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}
	.story ol {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}
	.pending {
		padding: var(--space-4);
		border: 1px dashed var(--rule-strong);
		border-radius: var(--radius-md);
		color: var(--muted);
	}
</style>
