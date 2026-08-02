<script lang="ts">
	/** Delay Sync's own dev harness (CLAUDE.md's Phase 3.3) — proves the full
	 * path end-to-end (`SessionStream` -> `DelaySyncController.ingest` ->
	 * `tick` -> `visibleState`) against a real replayed session, the same
	 * way `/` proves the REST path against `GET /sessions`. Not a race view:
	 * no track map, no degradation/undercut panel — those are still Phase
	 * 3's next, separate deliverable (docs/PHASE3.md). This route only
	 * demonstrates that nothing ahead of the playback clock ever reaches the
	 * `visibleState` a real view would subscribe to. */
	import { onDestroy, onMount } from 'svelte';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import DelayIndicator from '$lib/components/DelayIndicator.svelte';
	import DelaySlider from '$lib/components/DelaySlider.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import Stat from '$lib/components/Stat.svelte';
	import TapToSync from '$lib/components/TapToSync.svelte';
	import { DelaySyncController, type VisibleSessionState } from '$lib/delay-sync';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	let status = $state<'loading' | 'error' | 'ready'>('loading');
	let sessions = $state<SessionSummary[]>([]);
	let errorMessage = $state('');
	let selectedKey = $state<string | null>(null);
	let firstEventTimeUtc = $state<string | null>(null);

	const controller = new DelaySyncController({ initialDelaySeconds: 20 });
	let visible = $state<VisibleSessionState>(controller.visibleState.get());
	const unsubscribeVisible = controller.visibleState.subscribe((state) => (visible = state));

	let stream: SessionStream | null = null;
	let tickHandle: ReturnType<typeof setInterval> | null = null;

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
			errorMessage = 'Could not reach f1_api — is it running?';
		}
	}

	function disconnect(): void {
		stream?.close();
		stream = null;
		if (tickHandle !== null) clearInterval(tickHandle);
		tickHandle = null;
	}

	function connect(key: string): void {
		disconnect();
		selectedKey = key;
		firstEventTimeUtc = null;

		const session = new SessionStream(key, { mode: 'replay', speed: 20 });
		session.onMessage((envelope) => {
			if (firstEventTimeUtc === null) firstEventTimeUtc = envelope.event_time_utc;
			controller.ingest(envelope);
		});
		session.connect();
		stream = session;

		// Delay Sync's tick cadence: independent of the transport's own
		// pacing — the buffer holds everything until the playback clock says
		// it's due, so polling every 250ms is plenty responsive without
		// recomputing on every single WebSocket message.
		tickHandle = setInterval(() => controller.tick(), 250);
	}

	function onSessionChange(event: Event): void {
		const key = (event.currentTarget as HTMLSelectElement).value;
		if (key) connect(key);
	}

	onMount(loadSessions);
	onDestroy(() => {
		disconnect();
		unsubscribeVisible();
	});
</script>

<h1>Delay Sync</h1>
<p>
	Holds every timed value — including kernel snapshots — back to match a delayed broadcast. Nothing
	whose <code>event_time_utc</code> is ahead of your playback clock ever reaches the numbers below.
</p>

{#if status === 'loading'}
	<LoadingState label="Loading sessions…" />
{:else if status === 'error'}
	<Panel title="Couldn't load sessions">
		<p>{errorMessage}</p>
		<p class="hint">
			Start the API with <code>just serve-api</code> (see docs/PHASE3.md), then
			<button type="button" class="retry" onclick={loadSessions}>try again</button>.
		</p>
	</Panel>
{:else if sessions.length === 0}
	<Panel title="No sessions yet">
		<p>
			No historical backfill and no live session configured. Run <code>f1-backfill</code>
			(see docs/PHASE0.md) or point <code>F1_STORE_DSN</code> at a live-ingested session.
		</p>
	</Panel>
{:else}
	<Panel title="Session">
		<select value={selectedKey ?? ''} onchange={onSessionChange}>
			<option value="" disabled>Choose a session to replay…</option>
			{#each sessions as session (session.key)}
				<option value={session.key}>{session.event_name ?? session.key}</option>
			{/each}
		</select>
	</Panel>

	{#if selectedKey}
		<Panel title="Playback clock">
			<div class="controls-row">
				<DelaySlider {controller} />
				<DelayIndicator {controller} />
			</div>
			<TapToSync
				{controller}
				anchorEventTimeUtc={firstEventTimeUtc}
				label="Tap on this session's first event"
			/>
		</Panel>

		<Panel title="Visible state" subtitle="What the playback clock has released so far">
			<div class="stats-row">
				<Stat label="Visible events" value={visible.events.length} />
				<Stat
					label="Caught up to"
					value={visible.lastEventTimeUtc
						? new Date(visible.lastEventTimeUtc).toLocaleTimeString()
						: null}
				/>
				<Stat label="Latest decision lap" value={visible.latestSnapshot?.decision_lap ?? null} />
				<Stat
					label="Crossovers visible"
					value={visible.latestSnapshot ? visible.latestSnapshot.crossovers.length : null}
				/>
			</div>
		</Panel>
	{/if}
{/if}

<style>
	.hint {
		color: var(--color-text-muted);
		font-size: var(--font-size-sm);
	}

	.retry {
		background: none;
		border: none;
		padding: 0;
		color: var(--color-accent-strong);
		text-decoration: underline;
		cursor: pointer;
	}

	select {
		width: 100%;
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
	}

	.controls-row {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		flex-wrap: wrap;
	}

	.stats-row {
		display: flex;
		gap: var(--space-6);
		flex-wrap: wrap;
	}
</style>
