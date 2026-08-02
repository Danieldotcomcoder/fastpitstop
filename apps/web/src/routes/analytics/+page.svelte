<script lang="ts">
	/** The kernel-analytics views' own dev harness (CLAUDE.md's Phase 3.5) —
	 * the same role `/delay-sync` and `/track-map` play for their own
	 * prompts: proves `SessionStream -> DelaySyncController.ingest -> tick ->
	 * visibleState -> view` end-to-end against a real replayed session, for
	 * all four views at once. Mobile-first: a single stacked column, exactly
	 * how a phone held next to a TV would see it — no side-by-side desktop
	 * layout to fall back to. */
	import { onDestroy, onMount } from 'svelte';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import BrakingPointDrift from '$lib/components/BrakingPointDrift.svelte';
	import DegradationCurves from '$lib/components/DegradationCurves.svelte';
	import DelayIndicator from '$lib/components/DelayIndicator.svelte';
	import DelaySlider from '$lib/components/DelaySlider.svelte';
	import GapStreamGraph from '$lib/components/GapStreamGraph.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import UndercutCountdown from '$lib/components/UndercutCountdown.svelte';
	import { DelaySyncController } from '$lib/delay-sync';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	let sessionsStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let sessions = $state<SessionSummary[]>([]);
	let errorMessage = $state('');
	let selectedKey = $state<string | null>(null);

	let controller = $state(new DelaySyncController({ initialDelaySeconds: 20 }));

	let stream: SessionStream | null = null;
	let tickHandle: ReturnType<typeof setInterval> | null = null;

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

	function disconnect(): void {
		stream?.close();
		stream = null;
		if (tickHandle !== null) clearInterval(tickHandle);
		tickHandle = null;
	}

	function connect(key: string): void {
		disconnect();
		selectedKey = key;
		controller = new DelaySyncController({ initialDelaySeconds: 20 });

		// A fast, but not instant, replay: fast enough to watch a whole race's
		// stints resolve in minutes rather than hours, slow enough that the
		// spoiler gate's lap-by-lap reveal is still visible happening rather
		// than everything unlocking in one frame.
		const session = new SessionStream(key, { mode: 'replay', speed: 20 });
		session.onMessage((envelope) => controller.ingest(envelope));
		session.connect();
		stream = session;
		tickHandle = setInterval(() => controller.tick(), 250);
	}

	function onSessionChange(event: Event): void {
		const key = (event.currentTarget as HTMLSelectElement).value;
		if (key) connect(key);
	}

	onMount(loadSessions);
	onDestroy(disconnect);
</script>

<h1>Race analytics</h1>
<p>
	Phase 2's kernel output, rendered — every value carries its own confidence, and nothing here is
	ever ahead of the delay slider below. All four views read only <code>controller.visibleState</code
	>; the two that need a REST-fetched fit (<em>Degradation curves</em>,
	<em>Braking-point drift</em>) additionally gate on which stints have provably finished, so a
	whole-session fetch never leaks a future stint.
</p>

{#if sessionsStatus === 'loading'}
	<LoadingState label="Loading sessions…" />
{:else if sessionsStatus === 'error'}
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
		</Panel>

		<div class="views">
			<UndercutCountdown {controller} />
			<GapStreamGraph {controller} />
			<DegradationCurves apiKey={selectedKey} {controller} />
			<BrakingPointDrift apiKey={selectedKey} {controller} />
		</div>
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

	.views {
		display: flex;
		flex-direction: column;
		gap: var(--space-5);
	}
</style>
