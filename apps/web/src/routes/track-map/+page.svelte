<script lang="ts">
	/** The track map's own dev harness (Phase 3.4) — the same role
	 * `/delay-sync` plays for Delay Sync: proves `SessionStream ->
	 * DelaySyncController -> visibleState -> TrackMap` end-to-end against a
	 * real replayed session.
	 *
	 * Real sessions available in this environment carry no `car_position`
	 * data: historical (backfill) sessions never do (DATA_CONTRACTS.md —
	 * `f1_backfill` exports no position table), and a live/store-backed
	 * session needs a real TimescaleDB with a live-ingested race, neither of
	 * which exists here. "Synthetic preview" drives the exact same pipeline
	 * (`DelaySyncController.ingest` -> `tick` -> `visibleState` -> `TrackMap`)
	 * with a locally generated sequence of `car_position` envelopes on a
	 * stand-in oval shape, so the rendering/interpolation/dominance logic is
	 * still genuinely exercised end-to-end in a browser — clearly labeled as
	 * a preview, never presented as real telemetry. */
	import { onDestroy, onMount } from 'svelte';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import DelayIndicator from '$lib/components/DelayIndicator.svelte';
	import DelaySlider from '$lib/components/DelaySlider.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import TrackMap from '$lib/components/TrackMap.svelte';
	import { DelaySyncController } from '$lib/delay-sync';
	import { outlineFromApi, type TrackOutline } from '$lib/track-map';

	type SessionSummary = components['schemas']['SessionSummaryOut'];

	interface PreviewDriver {
		number: string;
		code: string;
		team: string;
		compound: string;
		lapSeconds: number;
		phase: number;
	}

	const PREVIEW_DRIVERS: PreviewDriver[] = [
		{ number: '1', code: 'VER', team: 'Red Bull Racing', compound: 'soft', lapSeconds: 11, phase: 0 },
		{ number: '44', code: 'HAM', team: 'Mercedes', compound: 'medium', lapSeconds: 13, phase: 0.28 },
		{ number: '16', code: 'LEC', team: 'Ferrari', compound: 'hard', lapSeconds: 12, phase: 0.6 }
	];

	let sessionsStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let sessions = $state<SessionSummary[]>([]);
	let errorMessage = $state('');
	let mode = $state<'preview' | 'session'>('preview');
	let selectedKey = $state<string | null>(null);
	let outline = $state<TrackOutline | null>(null);

	let controller = $state(new DelaySyncController({ initialDelaySeconds: 2 }));

	let stream: SessionStream | null = null;
	let tickHandle: ReturnType<typeof setInterval> | null = null;
	let previewHandle: ReturnType<typeof setInterval> | null = null;

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
		if (previewHandle !== null) clearInterval(previewHandle);
		previewHandle = null;
	}

	function syntheticPoint(fraction: number): [number, number] {
		// A stand-in stadium oval, NOT a real circuit shape — synthetic
		// preview only, used because no environment here has real
		// `car_position` data to render against (see the module doc above).
		const angle = fraction * 2 * Math.PI;
		return [Math.cos(angle) * 500, Math.sin(angle) * 250];
	}

	function syntheticOutline(): TrackOutline {
		const steps = 180;
		const points: [number, number][] = [];
		for (let i = 0; i <= steps; i++) points.push(syntheticPoint(i / steps));
		return { points, width_m: 100, height_m: 50, source_points: steps };
	}

	function connectPreview(): void {
		disconnect();
		mode = 'preview';
		selectedKey = null;
		controller = new DelaySyncController({ initialDelaySeconds: 2 });
		outline = syntheticOutline();

		const nowIso = () => new Date().toISOString();
		controller.ingest({
			event_time_utc: nowIso(),
			kind: 'driver_list_updated',
			payload: {
				drivers: PREVIEW_DRIVERS.map((d) => ({ number: d.number, code: d.code, team: d.team }))
			}
		});
		for (const driver of PREVIEW_DRIVERS) {
			controller.ingest({
				event_time_utc: nowIso(),
				kind: 'stint_started',
				payload: { driver: driver.number, compound: driver.compound }
			});
		}

		const startMs = Date.now();
		previewHandle = setInterval(() => {
			const nowMs = Date.now();
			const elapsedS = (nowMs - startMs) / 1000;
			for (const driver of PREVIEW_DRIVERS) {
				const fraction = (driver.phase + elapsedS / driver.lapSeconds) % 1;
				const [x, y] = syntheticPoint(fraction);
				controller.ingest({
					event_time_utc: new Date(nowMs).toISOString(),
					kind: 'car_position',
					payload: { driver: driver.number, x, y, z: 0 }
				});
			}
		}, 150);

		tickHandle = setInterval(() => controller.tick(), 100);
	}

	async function connectSession(key: string): Promise<void> {
		disconnect();
		mode = 'session';
		selectedKey = key;
		outline = null;
		controller = new DelaySyncController({ initialDelaySeconds: 20 });

		try {
			const { data } = await api.GET('/sessions/{key}/circuit', { params: { path: { key } } });
			outline = outlineFromApi(data?.outline);
		} catch {
			outline = null;
		}

		const session = new SessionStream(key, { mode: 'replay', speed: 20 });
		session.onMessage((envelope) => controller.ingest(envelope));
		session.connect();
		stream = session;
		tickHandle = setInterval(() => controller.tick(), 250);
	}

	function onSessionChange(event: Event): void {
		const key = (event.currentTarget as HTMLSelectElement).value;
		if (key) void connectSession(key);
	}

	onMount(() => {
		void loadSessions();
		connectPreview();
	});
	onDestroy(disconnect);
</script>

<h1>Track map</h1>
<p>
	Cars, rendered on Canvas from Delay Sync's <code>visibleState</code> only — never the raw stream.
	The colored arcs are sector/mini-sector dominance: whichever driver currently holds the fastest
	measured time through that stretch of track.
</p>

<Panel title="Source">
	<div class="mode-row">
		<button type="button" class:active={mode === 'preview'} onclick={connectPreview}>
			Synthetic preview
		</button>
		{#if sessionsStatus === 'ready' && sessions.length > 0}
			<select value={mode === 'session' ? (selectedKey ?? '') : ''} onchange={onSessionChange}>
				<option value="" disabled>Replay a real session…</option>
				{#each sessions as session (session.key)}
					<option value={session.key}>{session.event_name ?? session.key}</option>
				{/each}
			</select>
		{/if}
	</div>
	{#if mode === 'preview'}
		<p class="hint">
			Synthetic data: three cars looping a stand-in oval at different lap times, so the rendering
			pipeline is exercised end-to-end without needing a live/store-backed session — no real
			circuit or telemetry is shown in this mode.
		</p>
	{:else if outline === null}
		<p class="hint">
			This session has no shipped circuit outline yet (`tools/build_circuits.py`'s outline step
			needs a populated FastF1 cache to run for real), and/or no <code>car_position</code> data —
			historical sessions never carry it (see DATA_CONTRACTS.md).
		</p>
	{/if}
</Panel>

{#if sessionsStatus === 'error'}
	<Panel title="Couldn't load sessions">
		<p>{errorMessage}</p>
		<p class="hint">
			Start the API with <code>just serve-api</code> (see docs/PHASE3.md), then
			<button type="button" class="retry" onclick={loadSessions}>try again</button>.
		</p>
	</Panel>
{:else if sessionsStatus === 'loading'}
	<LoadingState label="Loading sessions…" />
{/if}

<Panel title="Playback clock">
	<div class="controls-row">
		<DelaySlider {controller} />
		<DelayIndicator {controller} />
	</div>
</Panel>

<TrackMap {controller} {outline} />

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

	.mode-row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		flex-wrap: wrap;
	}

	.mode-row button {
		padding: var(--space-2) var(--space-4);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		color: var(--color-text);
		font: inherit;
		cursor: pointer;
	}

	.mode-row button.active {
		border-color: var(--color-accent);
		color: var(--color-accent-strong);
	}

	select {
		flex: 1;
		min-width: 12rem;
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
</style>
