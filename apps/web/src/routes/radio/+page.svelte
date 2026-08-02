<script lang="ts">
	/** The corpus search view — the standalone "this dataset exists" feature
	 * (Phase 4.5): a real search product over the whole team-radio history,
	 * not tied to any one session. Driven entirely by `GET /radio/search`
	 * (`f1_api.routes.radio.search_radio`); this page owns none of the
	 * ranking or filtering logic itself — `$lib/radio`'s pure `search-params`
	 * module does the one bit worth unit-testing (which fields get sent,
	 * which get trimmed/omitted), this component just wires inputs to it and
	 * debounces the fetch. */
	import { api, type components } from '$lib/api/client';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import RadioClipCard from '$lib/components/RadioClipCard.svelte';
	import {
		buildRadioSearchQuery,
		hasAnyRadioFilter,
		radioClipViewFromApi,
		type RadioSearchFilters
	} from '$lib/radio';

	type SearchResult = components['schemas']['RadioSearchResultOut'];
	type Status = 'idle' | 'loading' | 'ready' | 'error';

	const DEBOUNCE_MS = 350;
	const EXAMPLE_QUERIES = [
		'complaining about front tyres',
		'box this lap',
		'gap to the car ahead'
	];

	let query = $state('');
	let driver = $state('');
	let session = $state('');
	let from = $state('');
	let to = $state('');
	let trackStatus = $state('');

	let filters = $derived<RadioSearchFilters>({ query, driver, session, from, to, trackStatus });

	let status = $state<Status>('idle');
	let results = $state<SearchResult[]>([]);
	let errorMessage = $state('');

	let debounceHandle: ReturnType<typeof setTimeout> | null = null;
	let requestId = 0;

	async function runSearch(current: RadioSearchFilters): Promise<void> {
		if (!hasAnyRadioFilter(current)) {
			status = 'idle';
			results = [];
			return;
		}
		const id = ++requestId;
		status = 'loading';
		try {
			const { data, error } = await api.GET('/radio/search', {
				params: { query: buildRadioSearchQuery(current) }
			});
			if (id !== requestId) return; // superseded by a later search
			if (error) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			results = data;
			status = 'ready';
		} catch {
			if (id !== requestId) return;
			status = 'error';
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	$effect(() => {
		const current = filters;
		if (debounceHandle !== null) clearTimeout(debounceHandle);
		debounceHandle = setTimeout(() => void runSearch(current), DEBOUNCE_MS);
		return () => {
			if (debounceHandle !== null) clearTimeout(debounceHandle);
		};
	});

	function tryExample(example: string): void {
		query = example;
	}

	function clearFilters(): void {
		driver = '';
		session = '';
		from = '';
		to = '';
		trackStatus = '';
	}
</script>

<h1>Team radio search</h1>
<p>
	Search team radio by MEANING, not just keywords — a paraphrase finds the right exchange even
	with no lexical overlap. Every result is enriched with race context: lap, classification
	position, and track status at the moment it was said.
</p>

<Panel title="Search">
	<input
		type="search"
		placeholder="e.g. complaining about front tyres"
		bind:value={query}
		class="query-input"
		data-testid="radio-search-query"
		aria-label="Search team radio"
	/>

	{#if status === 'idle'}
		<p class="hint">
			Try:
			{#each EXAMPLE_QUERIES as example (example)}
				<button type="button" class="example" onclick={() => tryExample(example)}>
					{example}
				</button>
			{/each}
		</p>
	{/if}

	<details class="filters">
		<summary>Filters (driver, session, date range, track status)</summary>
		<div class="filters-grid">
			<label class="field">
				<span>Driver</span>
				<input type="text" placeholder="44" bind:value={driver} data-testid="radio-filter-driver" />
			</label>
			<label class="field">
				<span>Session</span>
				<input
					type="text"
					placeholder="2024-1-race"
					bind:value={session}
					data-testid="radio-filter-session"
				/>
			</label>
			<label class="field">
				<span>Track status</span>
				<select bind:value={trackStatus} data-testid="radio-filter-track-status">
					<option value="">Any</option>
					<option value="green">Green</option>
					<option value="yellow">Yellow</option>
					<option value="sc">Safety car</option>
					<option value="vsc">Virtual safety car</option>
					<option value="red">Red flag</option>
				</select>
			</label>
			<label class="field">
				<span>From</span>
				<input type="date" bind:value={from} />
			</label>
			<label class="field">
				<span>To</span>
				<input type="date" bind:value={to} />
			</label>
		</div>
		{#if driver || session || from || to || trackStatus}
			<button type="button" class="clear" onclick={clearFilters}>Clear filters</button>
		{/if}
	</details>
</Panel>

{#if status === 'loading'}
	<LoadingState label="Searching…" />
{:else if status === 'error'}
	<Panel title="Search failed">
		<p>{errorMessage}</p>
	</Panel>
{:else if status === 'ready' && results.length === 0}
	<Panel title="No results">
		<p>No radio clips matched that search. Try a different phrase, or loosen the filters.</p>
	</Panel>
{:else if status === 'ready'}
	<div class="results" data-testid="radio-results">
		{#each results as result, i (result.clip.clip_id || i)}
			<RadioClipCard
				clip={radioClipViewFromApi(result.clip)}
				clipId={result.clip.clip_id}
				similarity={result.similarity}
			/>
		{/each}
	</div>
{/if}

<style>
	.query-input {
		width: 100%;
		padding: var(--space-3) var(--space-4);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
		font-size: var(--font-size-md);
	}

	.hint {
		margin: 0;
		font-size: var(--font-size-sm);
		color: var(--color-text-muted);
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.example {
		padding: var(--space-1) var(--space-3);
		border-radius: 999px;
		border: 1px solid var(--color-border-strong);
		background: none;
		color: var(--color-accent-strong);
		font: inherit;
		font-size: var(--font-size-xs);
		cursor: pointer;
	}

	.filters {
		font-size: var(--font-size-sm);
	}

	.filters summary {
		cursor: pointer;
		color: var(--color-text-muted);
	}

	.filters-grid {
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--space-3);
		margin-top: var(--space-3);
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
	}

	.field input,
	.field select {
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
	}

	.clear {
		margin-top: var(--space-3);
		background: none;
		border: none;
		padding: 0;
		color: var(--color-accent-strong);
		text-decoration: underline;
		cursor: pointer;
		font-size: var(--font-size-xs);
	}

	.results {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	@media (min-width: 640px) {
		.filters-grid {
			grid-template-columns: repeat(3, 1fr);
		}
	}
</style>
