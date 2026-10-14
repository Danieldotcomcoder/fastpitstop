<script lang="ts">
	/** One race, replayed. Resolves the session named by the URL and hands
	 * it to `RaceWorkspace` — the same component `/live` renders, so what
	 * you test against a replay is literally what a live session shows. */
	import { page } from '$app/state';
	import { api, type components } from '$lib/api/client';
	import { seoFor, toSeoSession } from '$lib/seo';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import RaceWorkspace from '$lib/session-detail/RaceWorkspace.svelte';

	type Session = components['schemas']['SessionSummaryOut'];

	let { data } = $props();

	let session = $state<Session | null>(null);
	/** Every session held at this round, for the links between them. */
	let weekend = $state<Session[]>([]);
	let status = $state<'loading' | 'ready' | 'error'>('loading');
	let error = $state('');
	let generation = 0;

	const year = $derived(Number(page.params.year));
	const round = $derived(Number(page.params.round));
	const wantedSession = $derived(page.url.searchParams.get('session'));

	// A different round, or another session at the same one, reloads. The
	// server sends the round's sessions with the page (`+page.server.ts`),
	// so the desk opens at once; the catalog is still read afterwards, in
	// case the server's copy (cached for seconds) missed a session going
	// live.
	$effect(() => {
		void [year, round, wantedSession];
		const sent = data.weekend.filter(
			(s) => s.session_key.year === year && s.session_key.round === round
		);
		void load(sent.length > 0 && pick(sent));
	});

	/** `?session=qualifying` picks that session; otherwise the race, the
	 * headline of a weekend, with anything else at that round a fallback
	 * rather than a 404. Shows it and says whether there was one. */
	function pick(matches: Session[]): boolean {
		const wanted = page.url.searchParams.get('session') ?? 'race';
		const found =
			matches.find((s) => s.session_key.session_type === wanted) ??
			matches.find((s) => s.session_key.session_type === 'race') ??
			matches[0] ??
			null;
		if (!found) return false;
		// Keep the same object when nothing changed, so the desk is not torn
		// down and reconnected by the background refresh.
		if (session?.key !== found.key || session.live !== found.live) session = found;
		weekend = matches;
		status = 'ready';
		return true;
	}

	async function load(quietly: boolean): Promise<void> {
		const own = ++generation;
		if (!quietly) status = 'loading';
		try {
			const result = await api.GET('/sessions');
			if (own !== generation) return;
			if (!result.data) throw new Error('Could not load sessions.');
			const matches = result.data.filter(
				(s) => s.session_key.year === year && s.session_key.round === round
			);
			if (!pick(matches)) throw new Error('This session is not available.');
		} catch (e) {
			// The page is already showing the race the server sent: a failed
			// refresh changes nothing.
			if (quietly) return;
			if (own !== generation) return;
			error = e instanceof Error ? e.message : 'Could not connect.';
			status = 'error';
		}
	}
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
	<button onclick={() => void load(false)}>Try again</button>
{:else if session}
	{#key `${session.key}:${session.live}`}
		<RaceWorkspace {session} {weekend} />
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
