<script lang="ts">
	/** One race, replayed. Resolves the session named by the URL and hands
	 * it to `RaceWorkspace` — the same component `/live` renders, so what
	 * you test against a replay is literally what a live session shows. */
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, type components } from '$lib/api/client';
	import { seoFor, toSeoSession } from '$lib/seo';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import RaceWorkspace from '$lib/session-detail/RaceWorkspace.svelte';

	type Session = components['schemas']['SessionSummaryOut'];

	let session = $state<Session | null>(null);
	let status = $state<'loading' | 'ready' | 'error'>('loading');
	let error = $state('');
	let generation = 0;

	const year = $derived(Number(page.params.year));
	const round = $derived(Number(page.params.round));

	async function load(): Promise<void> {
		const own = ++generation;
		status = 'loading';
		try {
			const result = await api.GET('/sessions');
			if (own !== generation) return;
			if (!result.data) throw new Error('Could not load sessions.');
			const matches = result.data.filter(
				(s) => s.session_key.year === year && s.session_key.round === round
			);
			// The race is the headline session of a weekend; anything else at
			// that round is a fallback rather than a 404.
			const found =
				matches.find((s) => s.session_key.session_type === 'race') ?? matches[0] ?? null;
			if (!found) throw new Error('This session is not available.');
			session = found;
			status = 'ready';
		} catch (e) {
			if (own !== generation) return;
			error = e instanceof Error ? e.message : 'Could not connect.';
			status = 'error';
		}
	}

	onMount(() => {
		void load();
	});
</script>

<!-- The same title the server writes into the HTML (`$lib/seo`), so the
     page search engines render keeps it. -->
<svelte:head
	><title>{seoFor(page.url.pathname, session ? [toSeoSession(session)] : []).title}</title
	></svelte:head
>

{#if status === 'loading'}
	<!-- Holds the desk's full width and the viewport while the session
	     resolves. A centred 640px loading column that then snapped to the
	     desk's full width was the largest single layout shift on the page
	     (CLS 0.26, `just verify-real`). -->
	<div class="opening desk-page"><LoadingState label="Opening the race…" /></div>
{:else if status === 'error'}
	<h1>Could not open the race</h1>
	<p>{error}</p>
	<button onclick={() => void load()}>Try again</button>
{:else if session}
	{#key session.key}
		<RaceWorkspace {session} />
	{/key}
{/if}

<style>
	.opening {
		min-height: 100dvh;
		padding: var(--space-5) var(--space-4);
	}

	button {
		font: inherit;
		padding: 0.65rem 0.9rem;
		border: 1px solid var(--hairline-strong);
		border-radius: var(--radius-sm);
		background: var(--surface-raised);
		color: var(--text-primary);
		cursor: pointer;
	}
</style>
