<script lang="ts">
	/** The product's front door. Polls `/sessions` for one with
	 * `provenance === 'live'` (f1_api's own signal that `StoreSource` — not
	 * backfill — is serving it, i.e. a session actually in progress) and, when
	 * found, mounts the exact same battle-tested dashboard components `/race`
	 * composes (TimingTower, UndercutCountdown, DegradationCurves,
	 * BrakingPointDrift, GapStreamGraph, RadioLane, ExplanationLane) driven by
	 * a real `mode: 'live'` stream connection instead of a replay. Registers
	 * its controller with `$lib/live-session` so the header's persistent Delay
	 * Sync badge appears — this is the one page where that badge matters most.
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
	import { SessionStream } from '$lib/api/stream';
	import BrakingPointDrift from '$lib/components/BrakingPointDrift.svelte';
	import DegradationCurves from '$lib/components/DegradationCurves.svelte';
	import ExplanationLane from '$lib/components/ExplanationLane.svelte';
	import GapStreamGraph from '$lib/components/GapStreamGraph.svelte';
	import RadioLane from '$lib/components/RadioLane.svelte';
	import TimingTower from '$lib/components/TimingTower.svelte';
	import TrackMap from '$lib/components/TrackMap.svelte';
	import UndercutCountdown from '$lib/components/UndercutCountdown.svelte';
	import { DelaySyncController } from '$lib/delay-sync';
	import { pickLiveSession } from '$lib/live-session-picker';
	import {
		clearActiveStreamSession,
		setActiveStreamSession,
		updateActiveStreamAnchor
	} from '$lib/live-session';
	import { outlineFromApi, type TrackOutline } from '$lib/track-map';

	type SessionSummary = components['schemas']['SessionSummaryOut'];
	type AnalyticsTab = 'gap' | 'degradation' | 'braking';

	const POLL_INTERVAL_MS = 15_000;
	const DEFAULT_DELAY_SECONDS = 20;
	const TABS: { id: AnalyticsTab; label: string }[] = [
		{ id: 'gap', label: 'Gap stream' },
		{ id: 'degradation', label: 'Degradation' },
		{ id: 'braking', label: 'Braking' }
	];

	let liveSession = $state<SessionSummary | null>(null);
	let checkedOnce = $state(false);
	let outline = $state<TrackOutline | null>(null);
	let activeTab = $state<AnalyticsTab>('gap');
	let controller = $state<DelaySyncController | null>(null);

	let stream: SessionStream | null = null;
	let tickHandle: ReturnType<typeof setInterval> | null = null;
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	let firstEventTimeUtc: string | null = null;

	function disconnect(): void {
		stream?.close();
		stream = null;
		if (tickHandle !== null) clearInterval(tickHandle);
		tickHandle = null;
		clearActiveStreamSession();
		controller = null;
	}

	function connectLive(session: SessionSummary): void {
		firstEventTimeUtc = null;
		const c = new DelaySyncController({ initialDelaySeconds: DEFAULT_DELAY_SECONDS });
		controller = c;
		setActiveStreamSession({
			controller: c,
			label: `${session.event_name ?? session.key} (live)`,
			anchorEventTimeUtc: null
		});

		api.GET('/sessions/{key}/circuit', { params: { path: { key: session.key } } })
			.then(({ data }) => (outline = outlineFromApi(data?.outline)))
			.catch(() => (outline = null));

		const socket = new SessionStream(session.key, { mode: 'live' });
		socket.onMessage((envelope) => {
			if (firstEventTimeUtc === null) {
				firstEventTimeUtc = envelope.event_time_utc;
				updateActiveStreamAnchor(envelope.event_time_utc);
			}
			c.ingest(envelope);
		});
		socket.connect();
		stream = socket;
		tickHandle = setInterval(() => c.tick(), 250);
	}

	async function poll(): Promise<void> {
		try {
			const { data } = await api.GET('/sessions');
			const found = pickLiveSession(data ?? []);
			checkedOnce = true;
			if (found && found.key !== liveSession?.key) {
				liveSession = found;
				disconnect();
				connectLive(found);
			} else if (!found && liveSession) {
				liveSession = null;
				disconnect();
			} else if (!found) {
				liveSession = null;
			}
		} catch {
			checkedOnce = true;
		}
	}

	onMount(() => {
		void poll();
		pollHandle = setInterval(() => void poll(), POLL_INTERVAL_MS);
	});

	onDestroy(() => {
		if (pollHandle !== null) clearInterval(pollHandle);
		disconnect();
	});
</script>

<svelte:head>
	<title>Live · {brand.name}</title>
</svelte:head>

{#if liveSession && controller}
	<div class="wide-dashboard live-dashboard">
		<header class="live-header">
			<p class="eyebrow tabular">LIVE</p>
			<h1>{liveSession.event_name ?? liveSession.key}</h1>
			{#if liveSession.circuit_name}<p class="circuit">{liveSession.circuit_name}</p>{/if}
		</header>

		<div class="grid">
			<div class="cell cell-tower">
				<TimingTower {controller} />
				<ExplanationLane {controller} />
			</div>
			<div class="cell cell-undercut">
				<UndercutCountdown {controller} />
			</div>
			<div class="cell cell-map">
				<h2 class="cell-heading">Track map</h2>
				<TrackMap {controller} {outline} />
			</div>
			<div class="cell cell-analytics">
				<div class="tabs" role="tablist" aria-label="Analytics view">
					{#each TABS as tab (tab.id)}
						<button
							type="button"
							role="tab"
							aria-selected={activeTab === tab.id}
							class:active={activeTab === tab.id}
							onclick={() => (activeTab = tab.id)}
						>
							{tab.label}
						</button>
					{/each}
				</div>
				{#if activeTab === 'gap'}
					<GapStreamGraph {controller} />
					<RadioLane {controller} />
				{:else if activeTab === 'degradation'}
					<DegradationCurves apiKey={liveSession.key} {controller} />
				{:else if activeTab === 'braking'}
					<BrakingPointDrift apiKey={liveSession.key} {controller} />
				{/if}
			</div>
		</div>
	</div>
{:else}
	<section class="idle">
		<h1>Live</h1>
		<p>
			During a session this page shows the running order, pit window, degradation, race
			control, and radio — all held back to your Delay Sync offset so nothing spoils.
		</p>
		<p class="note">
			{#if checkedOnce}
				No session is running right now. Browse a completed race while you wait, or open the
				engineering harness if you are replaying locally.
			{:else}
				Checking for a live session…
			{/if}
		</p>
		<p class="actions">
			<a href={resolve('/sessions')}>Browse sessions</a>
			<span class="sep" aria-hidden="true">·</span>
			<a href={resolve('/race')}>Open race harness</a>
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

	.sep {
		color: var(--text-muted);
	}

	.live-header {
		margin-bottom: var(--space-5);
	}

	.eyebrow {
		margin: 0 0 var(--space-2);
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
		color: var(--timing-green);
		text-transform: uppercase;
		letter-spacing: 0.08em;
	}

	.eyebrow::before {
		content: '';
		display: inline-block;
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		background: var(--timing-green);
		margin-right: var(--space-2);
	}

	.circuit {
		margin: var(--space-1) 0 0;
		color: var(--text-secondary);
	}

	.grid {
		display: grid;
		gap: var(--space-4);
		grid-template-columns: 1fr;
	}

	.cell {
		border: 1px solid var(--hairline);
		background: var(--surface-raised);
		padding: var(--space-4);
	}

	.cell-heading {
		font-size: var(--font-size-md);
		margin-bottom: var(--space-3);
	}

	.tabs {
		display: flex;
		gap: var(--space-2);
		margin-bottom: var(--space-3);
		border-bottom: 1px solid var(--hairline);
	}

	.tabs button {
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		padding: var(--space-2) var(--space-1);
		color: var(--text-muted);
		font: inherit;
		font-weight: 500;
		cursor: pointer;
	}

	.tabs button.active {
		color: var(--text-primary);
		border-bottom-color: var(--hairline-strong);
	}

	@media (min-width: 1025px) {
		.grid {
			grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
		}

		.cell-tower {
			grid-column: 1;
			grid-row: 1 / span 2;
		}

		.cell-undercut {
			grid-column: 2;
			grid-row: 1;
		}

		.cell-map {
			grid-column: 2;
			grid-row: 2;
		}

		.cell-analytics {
			grid-column: 1 / -1;
		}
	}
</style>
