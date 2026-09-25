<script lang="ts">
	import { SHOW_RACE_STORY, SHOW_RADIO } from '$lib/features';
	/** The race dashboard (Phase 3.6) — composes every Phase 3 race view onto
	 * one screen: the timing tower (`$lib/timing-tower`, new here), the
	 * undercut/overcut countdown (3.5), the track map (3.4), and a switchable
	 * analytics panel over degradation/gap-stream/braking (3.5). Every panel
	 * reads `controller.visibleState` only — the same "views may never touch
	 * the buffer or a raw stream directly" rule every dev harness above this
	 * one already follows — so composing them here adds no new spoiler-safety
	 * surface, it only arranges views that were each already provably safe.
	 *
	 * Doubles as the "demoable on a weekday" session picker: pick any
	 * historical session and a replay speed and the whole dashboard runs in
	 * replay-as-live mode, no live race required (mirrors `/track-map` and
	 * `/analytics`'s own harnesses, but is the one place `speed` is a picker
	 * rather than hardcoded, and the choice round-trips through the URL so a
	 * session is directly linkable — including for Playwright, which drives
	 * this page via `?key=&speed=` rather than the `<select>`s themselves). */
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import BrakingPointDrift from '$lib/components/BrakingPointDrift.svelte';
	import DegradationCurves from '$lib/components/DegradationCurves.svelte';
	import DelayIndicator from '$lib/components/DelayIndicator.svelte';
	import DelaySlider from '$lib/components/DelaySlider.svelte';
	import GapStreamGraph from '$lib/components/GapStreamGraph.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import ExplanationLane from '$lib/components/ExplanationLane.svelte';
	import RadioLane from '$lib/components/RadioLane.svelte';
	import Stat from '$lib/components/Stat.svelte';
	import TapToSync from '$lib/components/TapToSync.svelte';
	import TimingTower from '$lib/components/TimingTower.svelte';
	import TrackMap from '$lib/components/TrackMap.svelte';
	import UndercutCountdown from '$lib/components/UndercutCountdown.svelte';
	import {
		DelaySyncController,
		EMPTY_VISIBLE_STATE,
		playbackClockMs,
		type DelayState,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import { outlineFromApi, type TrackOutline } from '$lib/track-map';

	type SessionSummary = components['schemas']['SessionSummaryOut'];
	type AnalyticsTab = 'gap' | 'degradation' | 'braking';

	const SPEED_OPTIONS = [
		{ value: 1, label: '1x (real time)' },
		{ value: 5, label: '5x' },
		{ value: 20, label: '20x' },
		{ value: 50, label: '50x' },
		{ value: 0, label: 'Max speed' }
	];
	const DEFAULT_SPEED = 20;
	const DEFAULT_DELAY_SECONDS = 20;

	const TABS: { id: AnalyticsTab; label: string }[] = [
		{ id: 'gap', label: 'Gap stream' },
		{ id: 'degradation', label: 'Degradation' },
		{ id: 'braking', label: 'Braking' }
	];

	let sessionsStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let sessions = $state<SessionSummary[]>([]);
	let errorMessage = $state('');

	let selectedKey = $state<string | null>(null);
	let selectedSpeed = $state(DEFAULT_SPEED);
	let outline = $state<TrackOutline | null>(null);
	let firstEventTimeUtc = $state<string | null>(null);
	let activeTab = $state<AnalyticsTab>('gap');
	let sessionSelectEl: HTMLSelectElement | undefined = $state();
	let speedSelectEl: HTMLSelectElement | undefined = $state();

	$effect(() => {
		// A plain `value={x}` attribute on a native <select> (as opposed to
		// `bind:value`) is just an HTML ATTRIBUTE, which a <select> element
		// does not use to decide its selection at all — only the `.value`
		// PROPERTY (or an `<option selected>`) does. Setting it explicitly
		// here, imperatively, is what actually selects the matching option
		// for a URL-driven `connect()` (`bind:value` was avoided instead
		// because `selectedKey`'s `null` initial state doesn't coerce to a
		// sensible option value). Reading `sessions` (even though only its
		// LENGTH matters) additionally re-runs this once the async-fetched
		// `<option>`s actually exist, so a URL-driven key set before they
		// render still gets picked up the moment they do.
		void sessions.length;
		if (sessionSelectEl) sessionSelectEl.value = selectedKey ?? '';
	});
	$effect(() => {
		if (speedSelectEl) speedSelectEl.value = String(selectedSpeed);
	});

	let controller = $state(new DelaySyncController({ initialDelaySeconds: DEFAULT_DELAY_SECONDS }));
	// Raw, not deep: the released state is replaced, never mutated, and a
	// deep proxy made every reducer's read of every event a proxy trap
	// (16% of CPU at 16x on the live-recorded 2026 race, frames dropped).
	let visible = $state.raw<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	// A placeholder only — reading `controller.delayState.get()` here would just
	// capture today's controller once and go stale the moment `connect()`
	// swaps in a new one (Svelte's own `state_referenced_locally` warning).
	// The `$effect` below overwrites this synchronously on mount and on every
	// later `controller` reassignment, since `Store.subscribe` always calls
	// its listener immediately with the current value.
	let delayState = $state<DelayState>({ delaySeconds: DEFAULT_DELAY_SECONDS, synced: false });

	$effect(() => {
		// Re-subscribes whenever `controller` is reassigned (a new session
		// picks a fresh controller) — the same pattern every view above this
		// one already uses.
		return controller.visibleState.subscribe((state) => (visible = state));
	});
	$effect(() => {
		return controller.delayState.subscribe((state) => (delayState = state));
	});

	// A diagnostics-only wall-clock sample, independent of the tick that
	// actually releases envelopes (mirrors `TrackMap.svelte`'s own
	// `playbackClockMs(Date.now(), ...)` read) — purely so this page can show
	// its own proof that nothing released is ever ahead of it.
	let nowMs = $state(Date.now());
	$effect(() => {
		const handle = setInterval(() => (nowMs = Date.now()), 250);
		return () => clearInterval(handle);
	});
	let playbackClockNowMs = $derived(playbackClockMs(nowMs, delayState.delaySeconds));

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

	async function loadOutline(key: string): Promise<void> {
		try {
			const { data } = await api.GET('/sessions/{key}/circuit', { params: { path: { key } } });
			outline = outlineFromApi(data?.outline);
		} catch {
			outline = null;
		}
	}

	function disconnect(): void {
		stream?.close();
		stream = null;
		if (tickHandle !== null) clearInterval(tickHandle);
		tickHandle = null;
	}

	function connect(key: string, speed: number, delaySeconds: number = DEFAULT_DELAY_SECONDS): void {
		disconnect();
		selectedKey = key;
		selectedSpeed = speed;
		firstEventTimeUtc = null;
		outline = null;
		activeTab = 'gap';
		controller = new DelaySyncController({ initialDelaySeconds: delaySeconds });
		void loadOutline(key);

		const session = new SessionStream(key, { mode: 'replay', speed });
		session.onMessage((envelope) => {
			if (firstEventTimeUtc === null) firstEventTimeUtc = envelope.event_time_utc;
			controller.ingest(envelope);
		});
		session.connect();
		stream = session;
		tickHandle = setInterval(() => controller.tick(), 250);
	}

	/** Reflects the current session/speed into the URL (`replaceState`, no
	 * history spam) so the exact demo is linkable — and so Playwright can
	 * drive this page with a URL instead of waiting on the sessions list to
	 * populate before it can interact with a `<select>`. The pathname is the
	 * literal `/race` (this page can only ever be itself) rather than read
	 * back off `window.location`, precisely so `resolve()` can validate it
	 * against a real route at compile time. */
	function syncUrl(key: string, speed: number): void {
		const params = new URLSearchParams({ key, speed: String(speed) });
		void goto(resolve(`/race?${params.toString()}`), {
			replaceState: true,
			noScroll: true,
			keepFocus: true
		});
	}

	function selectSession(key: string, speed: number): void {
		connect(key, speed);
		syncUrl(key, speed);
	}

	function onSessionChange(event: Event): void {
		const key = (event.currentTarget as HTMLSelectElement).value;
		if (key) selectSession(key, selectedSpeed);
	}

	function onSpeedChange(event: Event): void {
		const speed = Number((event.currentTarget as HTMLSelectElement).value);
		if (selectedKey) selectSession(selectedKey, speed);
		else selectedSpeed = speed;
	}

	onMount(() => {
		void loadSessions();
		const params = new URLSearchParams(window.location.search);
		const initialKey = params.get('key');
		const speedParam = Number(params.get('speed'));
		const initialSpeed =
			Number.isFinite(speedParam) && params.has('speed') ? speedParam : DEFAULT_SPEED;
		const delayParam = Number(params.get('delay'));
		const initialDelay =
			Number.isFinite(delayParam) && params.has('delay') ? delayParam : DEFAULT_DELAY_SECONDS;
		selectedSpeed = initialSpeed;
		// `delay` is a deep-link-only convenience (e.g. for a fast, deterministic
		// Playwright run) — the slider is the ongoing UX for changing it, so
		// this is read once at mount rather than kept in sync with the URL.
		if (initialKey) connect(initialKey, initialSpeed, initialDelay);
	});
	onDestroy(disconnect);
</script>

<h1>Race dashboard</h1>
<p>
	Track map, timing tower, undercut countdown and kernel analytics — one screen, all of it gated
	behind your playback clock. Pick any historical session below and replay it at a chosen speed; no
	live race required.
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
{/if}

<Panel title="Session">
	<div class="picker-row">
		<label class="picker-field">
			<span>Session</span>
			<select
				bind:this={sessionSelectEl}
				data-testid="session-select"
				value={selectedKey ?? ''}
				onchange={onSessionChange}
			>
				<option value="" disabled>Choose a session to replay…</option>
				{#each sessions as session (session.key)}
					<option value={session.key}>{session.event_name ?? session.key}</option>
				{/each}
			</select>
		</label>
		<label class="picker-field">
			<span>Replay speed</span>
			<select
				bind:this={speedSelectEl}
				data-testid="speed-select"
				value={String(selectedSpeed)}
				onchange={onSpeedChange}
			>
				{#each SPEED_OPTIONS as opt (opt.value)}
					<option value={opt.value}>{opt.label}</option>
				{/each}
			</select>
		</label>
	</div>
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

	<div class="wide-dashboard">
		<div class="grid">
			<div class="cell cell-tower" data-testid="tower-panel">
				<TimingTower
					{controller}
					year={selectedKey ? Number(selectedKey.split('-')[0]) : undefined}
				/>
				{#if SHOW_RACE_STORY}<ExplanationLane {controller} />{/if}
			</div>
			<div class="cell cell-undercut" data-testid="undercut-panel">
				<UndercutCountdown {controller} />
			</div>
			<div class="cell cell-map" data-testid="map-panel">
				<h2 class="cell-heading">Track map</h2>
				<TrackMap {controller} {outline} />
			</div>
			<div class="cell cell-analytics" data-testid="analytics-panel">
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
					{#if SHOW_RADIO}<RadioLane {controller} />{/if}
				{:else if activeTab === 'degradation'}
					<DegradationCurves apiKey={selectedKey} {controller} />
				{:else if activeTab === 'braking'}
					<BrakingPointDrift apiKey={selectedKey} {controller} />
				{/if}
			</div>
		</div>
	</div>

	<Panel
		title="Sync diagnostics"
		subtitle="The proof: nothing releases ahead of your delayed clock"
	>
		<div class="stats-row">
			<span data-testid="sync-visible-events" class="visually-hidden">{visible.events.length}</span>
			<Stat label="Visible events" value={visible.events.length} />
			<Stat
				label="Caught up to"
				value={visible.lastEventTimeUtc
					? new Date(visible.lastEventTimeUtc).toLocaleTimeString()
					: null}
			/>
			<Stat label="Playback clock" value={new Date(playbackClockNowMs).toLocaleTimeString()} />
		</div>
		<p class="raw-hint">
			Machine-readable: last released event
			<span data-testid="sync-last-event-ms" class="font-mono"
				>{visible.lastEventTimeUtc ? Date.parse(visible.lastEventTimeUtc) : -1}</span
			>
			vs playback clock
			<span data-testid="sync-clock-ms" class="font-mono">{playbackClockNowMs}</span>
			(epoch ms) — spoiler-safe iff the former never exceeds the latter.
		</p>
	</Panel>
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

	.picker-row {
		display: flex;
		gap: var(--space-4);
		flex-wrap: wrap;
	}

	.picker-field {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font-size: var(--font-size-xs);
		color: var(--color-text-muted);
		flex: 1;
		min-width: 12rem;
	}

	select {
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

	.grid {
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--space-5);
	}

	.cell-heading {
		margin-bottom: 0;
	}

	.tabs {
		display: flex;
		gap: var(--space-2);
		flex-wrap: wrap;
	}

	.tabs button {
		padding: var(--space-2) var(--space-4);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text-muted);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}

	.tabs button.active {
		border-color: var(--color-accent);
		color: var(--color-accent-strong);
	}

	.cell-tower {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.cell-analytics {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.stats-row {
		display: flex;
		gap: var(--space-6);
		flex-wrap: wrap;
	}

	.raw-hint {
		margin: 0;
		font-size: var(--font-size-xs);
		color: var(--color-text-faint);
	}

	@media (min-width: 900px) {
		.grid {
			grid-template-columns: 1.15fr 0.85fr;
			grid-template-areas:
				'tower undercut'
				'tower map'
				'analytics analytics';
			align-items: start;
		}

		.cell-tower {
			grid-area: tower;
		}
		.cell-undercut {
			grid-area: undercut;
		}
		.cell-map {
			grid-area: map;
		}
		.cell-analytics {
			grid-area: analytics;
		}
	}
</style>
