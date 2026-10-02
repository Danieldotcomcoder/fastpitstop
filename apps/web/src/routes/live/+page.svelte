<script lang="ts">
	/** The product's front door. Polls `/sessions` for one flagged `live`
	 * (f1_api's own measured answer to "is this session happening right
	 * now" — see `pickLiveSession`, and note it is deliberately NOT
	 * `provenance`) and, when found, renders `RaceWorkspace`: the EXACT
	 * component `/sessions/[year]/[round]` renders for a replay.
	 *
	 * That shared component is the point. This page used to compose its own
	 * arrangement of the same panels, so "it works in a replay" said very
	 * little about live — the two drifted apart panel by panel. Now the only
	 * difference is the one that genuinely exists: `RaceWorkspace` sees
	 * `live`, so it opens a `mode: 'live'` stream and drops the
	 * playback-speed and restart controls.
	 *
	 * No live SignalR ingest is wired into this repo yet (CLAUDE.md's own
	 * Phase 3 "next" note), so the live branch below is real code with no way
	 * to exercise it against a genuine race from this environment — covered
	 * instead by `live-session-picker.test.ts`'s pure-logic test of
	 * `pickLiveSession`. The idle branch (no session live) is the common case
	 * and stays the primary thing a visitor sees. */
	import { onDestroy, onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { brand } from '$lib/brand';
	import { api, type components } from '$lib/api/client';
	import { pickLiveSession } from '$lib/live-session-picker';
	import RaceWorkspace from '$lib/session-detail/RaceWorkspace.svelte';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	const POLL_INTERVAL_MS = 15_000;
	/** A poll that has not answered in this long has failed, as far as this
	 * page is concerned. Without it a slow API leaves the page on
	 * "Checking for a live session…" forever with nothing to click and no
	 * indication anything is wrong — which is exactly what a starved API
	 * did in production. A bounded wait turns that into a visible,
	 * retrying error state. */
	const POLL_TIMEOUT_MS = 8_000;

	type Status = 'checking' | 'ready' | 'unreachable';

	let liveSession = $state<SessionSummary | null>(null);
	let status = $state<Status>('checking');
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	async function poll(): Promise<void> {
		try {
			const { data, error } = await api.GET('/sessions', {
				signal: AbortSignal.timeout(POLL_TIMEOUT_MS)
			});
			if (error !== undefined) throw new Error('sessions request failed');
			const found = pickLiveSession(data ?? []);
			status = 'ready';
			// Only ever switch to ANOTHER running session, never back to
			// nothing: a session stops counting as live once the feed says it
			// is over, and a viewer on a broadcast delay is still watching its
			// last laps then. The finished race stays on screen until the next
			// session starts. Compare by key: re-assigning an equal session
			// would re-key the workspace below and restart its stream.
			if (found !== null && found.key !== liveSession?.key) liveSession = found;
		} catch {
			// Keep showing a session we already found: one failed poll during
			// a race should not tear down a working dashboard.
			if (liveSession === null) status = 'unreachable';
		}
	}

	onMount(() => {
		void poll();
		pollHandle = setInterval(() => void poll(), POLL_INTERVAL_MS);
	});

	onDestroy(() => {
		if (pollHandle !== null) clearInterval(pollHandle);
	});
</script>

<svelte:head>
	<title>Live · {brand.name}</title>
</svelte:head>

{#if liveSession}
	{#key liveSession.key}
		<RaceWorkspace session={liveSession} showBackLink={false} />
	{/key}
{:else}
	<section class="idle">
		<h1>Live</h1>
		<p>
			During a session this page shows the running order, pit window, degradation, race control, and
			radio — all held back to your Delay Sync offset so nothing spoils.
		</p>
		<p class="note" class:error={status === 'unreachable'}>
			{#if status === 'ready'}
				No session is running right now. Browse a completed race while you wait — a replay shows
				exactly the same dashboard.
			{:else if status === 'unreachable'}
				Couldn't reach the API. Retrying every {POLL_INTERVAL_MS / 1000} seconds.
			{:else}
				Checking for a live session…
			{/if}
		</p>
		<p class="actions">
			<a href={resolve('/sessions')}>Browse sessions</a>
		</p>
	</section>
{/if}

<style>
	.idle h1 {
		margin-bottom: var(--space-2);
	}

	.note {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}

	.note.error {
		color: var(--flag-red);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		align-items: center;
		font-size: var(--font-size-sm);
	}

	.actions a {
		text-decoration: none;
		border-bottom: 1px solid var(--hairline-strong);
		color: var(--text-primary);
	}
</style>
