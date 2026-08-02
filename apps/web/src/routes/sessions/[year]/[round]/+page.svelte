<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { resolve } from '$app/paths';
	import { brand } from '$lib/brand';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import ExplanationCard from '$lib/components/ExplanationCard.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import Panel from '$lib/components/Panel.svelte';
	import BrakingPointDrift from '$lib/components/BrakingPointDrift.svelte';
	import DegradationCurves from '$lib/components/DegradationCurves.svelte';
	import DelayIndicator from '$lib/components/DelayIndicator.svelte';
	import DelaySlider from '$lib/components/DelaySlider.svelte';
	import RadioClipCard from '$lib/components/RadioClipCard.svelte';
	import SectionSkeleton from '$lib/components/SectionSkeleton.svelte';
	import SessionSubNav from '$lib/components/SessionSubNav.svelte';
	import ShortcutSheet from '$lib/components/ShortcutSheet.svelte';
	import TapToSync from '$lib/components/TapToSync.svelte';
	import { DelaySyncController } from '$lib/delay-sync';
	import {
		clearActiveStreamSession,
		setActiveStreamSession,
		updateActiveStreamAnchor
	} from '$lib/live-session';
	import { radioClipViewFromApi, summarizeRadioCorpus } from '$lib/radio';
	import {
		driversParam,
		parseDriversParam,
		SESSION_SECTIONS,
		type SessionSectionId
	} from '$lib/session-detail';
	import CrossoverRetrospective from '$lib/session-detail/CrossoverRetrospective.svelte';
	import RaceShapeChart from '$lib/session-detail/RaceShapeChart.svelte';
	import StrategyBoard from '$lib/session-detail/StrategyBoard.svelte';
	import {
		buildStoryMoments,
		explanationViewFromApi,
		type ExplanationView,
		type StoryMoment
	} from '$lib/why';

	type SessionSummary = components['schemas']['SessionSummaryOut'];
	type AnomalyOut = components['schemas']['AnomalyOut'];
	type ClassificationEntry = components['schemas']['ClassificationEntryOut'];
	type LapRow = components['schemas']['LapRowOut'];
	type Crossover = components['schemas']['CrossoverResultOut'];

	let status = $state<'loading' | 'error' | 'ready'>('loading');
	let errorMessage = $state('');
	let session = $state<SessionSummary | null>(null);
	let sessionKey = $state<string | null>(null);
	let classification = $state<ClassificationEntry[]>([]);
	let selectedDrivers = $state<string[]>([]);
	let activeSection = $state<SessionSectionId>('story');
	let shortcutsOpen = $state(false);
	/** Collapsed on mobile (a 20-row checkbox list shouldn't push every
	 * section below the fold), expanded as a normal sidebar on desktop. */
	let driversOpen = $state(false);
	let radioSearchEl: HTMLInputElement | undefined = $state();

	let storyStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let moments = $state<StoryMoment[]>([]);
	let radioQuery = $state('');
	let heavyReady = $state(false);
	let radioStatus = $state<'idle' | 'loading' | 'ready' | 'error'>('idle');
	let radioClips = $state<
		{ view: ReturnType<typeof radioClipViewFromApi>; clipId: string | null }[]
	>([]);
	let showPendingClips = $state(false);

	let lapsStatus = $state<'loading' | 'error' | 'ready'>('loading');
	let laps = $state<LapRow[]>([]);
	let crossovers = $state<Crossover[]>([]);
	let crossoverStatus = $state<'loading' | 'error' | 'ready'>('loading');

	/** A completed race replayed back through its own real event stream, the
	 * same way `/race` does — so a visitor who hasn't watched this session
	 * yet can still browse it without the page dumping the whole result on
	 * them at once. `DegradationCurves`/`BrakingPointDrift` gate on this for
	 * real (`RevealTracker`); `Race story`/`Strategy`/`Radio` below are still
	 * whole-session REST reads and are NOT yet gated by it — flagged in the
	 * Method section rather than silently implied to be safe. */
	const SPEED_OPTIONS = [
		{ value: 1, label: '1x (real time)' },
		{ value: 5, label: '5x' },
		{ value: 20, label: '20x' },
		{ value: 50, label: '50x' },
		{ value: 0, label: 'Max speed' }
	];
	const DEFAULT_SPEED = 20;
	const DEFAULT_DELAY_SECONDS = 20;

	let controller = $state(new DelaySyncController({ initialDelaySeconds: DEFAULT_DELAY_SECONDS }));
	let selectedSpeed = $state(DEFAULT_SPEED);
	let firstEventTimeUtc = $state<string | null>(null);
	let stream: SessionStream | null = null;
	let tickHandle: ReturnType<typeof setInterval> | null = null;

	function disconnectStream(): void {
		stream?.close();
		stream = null;
		if (tickHandle !== null) clearInterval(tickHandle);
		tickHandle = null;
		clearActiveStreamSession();
	}

	function connectStream(key: string, eventName: string, speed: number): void {
		disconnectStream();
		firstEventTimeUtc = null;
		const c = new DelaySyncController({ initialDelaySeconds: DEFAULT_DELAY_SECONDS });
		controller = c;
		setActiveStreamSession({ controller: c, label: `${eventName} (replay)`, anchorEventTimeUtc: null });

		const socket = new SessionStream(key, { mode: 'replay', speed });
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

	function onSpeedChange(event: Event): void {
		const speed = Number((event.currentTarget as HTMLSelectElement).value);
		selectedSpeed = speed;
		if (sessionKey && session) connectStream(sessionKey, session.event_name ?? sessionKey, speed);
	}


	const year = $derived(Number(page.params.year));
	const round = $derived(Number(page.params.round));

	function syncUrl(drivers: string[], hash?: string): void {
		if (!session) return;
		const params = new URLSearchParams();
		if (drivers.length) params.set('drivers', driversParam(drivers));
		const qs = params.toString();
		const path = resolve(`/sessions/${year}/${round}`);
		const next = `${path}${qs ? `?${qs}` : ''}${hash ?? page.url.hash}`;
		void goto(next, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function toggleDriver(driver: string): void {
		const next = selectedDrivers.includes(driver)
			? selectedDrivers.filter((d) => d !== driver)
			: [...selectedDrivers, driver];
		selectedDrivers = next;
		syncUrl(next);
	}

	async function loadStory(key: string): Promise<void> {
		storyStatus = 'loading';
		moments = [];
		try {
			const { data: anomalies, error } = await api.GET('/sessions/{key}/anomalies', {
				params: { path: { key } }
			});
			if (error || !anomalies) {
				storyStatus = 'error';
				return;
			}
			const explanationsById = new Map<string, ExplanationView>();
			await Promise.all(
				anomalies.map(async (anomaly: AnomalyOut) => {
					const { data, error: explError } = await api.GET(
						'/anomalies/{anomaly_id}/explanation',
						{ params: { path: { anomaly_id: anomaly.anomaly_id } } }
					);
					if (!explError && data) {
						explanationsById.set(anomaly.anomaly_id, explanationViewFromApi(data));
					}
				})
			);
			moments = buildStoryMoments(anomalies, explanationsById);
			storyStatus = 'ready';
		} catch {
			storyStatus = 'error';
		}
	}

	async function load(): Promise<void> {
		status = 'loading';
		session = null;
		try {
			const { data, error } = await api.GET('/sessions');
			if (error || !data) {
				status = 'error';
				errorMessage = 'The API responded with an error.';
				return;
			}
			const matches = data.filter(
				(s) => s.session_key.year === year && s.session_key.round === round
			);
			session = matches.find((s) => s.session_key.session_type === 'race') ?? matches[0] ?? null;
			if (!session) {
				status = 'error';
				errorMessage = `No session for ${year} round ${round}.`;
				return;
			}
			sessionKey = session.key;
			status = 'ready';
			connectStream(session.key, session.event_name ?? session.key, selectedSpeed);

			const fromUrl = parseDriversParam(page.url.searchParams.get('drivers'));
			selectedDrivers = fromUrl;

			const hash = page.url.hash.replace('#', '') as SessionSectionId;
			if (SESSION_SECTIONS.some((s) => s.id === hash)) activeSection = hash;

			void loadStory(session.key);

			const detail = await api.GET('/sessions/{key}', {
				params: { path: { key: session.key } }
			});
			if (detail.data) {
				classification = detail.data.classification;
				if (selectedDrivers.length === 0) {
					selectedDrivers = classification
						.filter((c) => c.position !== null && c.position <= 3)
						.map((c) => c.driver)
						.slice(0, 3);
					syncUrl(selectedDrivers, page.url.hash || '#story');
				}
			}

			// Heavier analytics mount after first paint so story/shape don't wait.
			window.setTimeout(() => (heavyReady = true), 50);
			void loadRadio(session.key, '');
			void loadLaps(session.key);
			void loadCrossovers(session.key);
		} catch {
			status = 'error';
			errorMessage = 'Could not reach the API — is it running?';
		}
	}

	async function loadLaps(key: string): Promise<void> {
		lapsStatus = 'loading';
		try {
			const { data, error } = await api.GET('/sessions/{key}/laps', { params: { path: { key } } });
			if (error || !data) {
				lapsStatus = 'error';
				return;
			}
			laps = data;
			lapsStatus = 'ready';
		} catch {
			lapsStatus = 'error';
		}
	}

	async function loadCrossovers(key: string): Promise<void> {
		crossoverStatus = 'loading';
		try {
			const { data, error } = await api.GET('/sessions/{key}/analysis', {
				params: { path: { key } }
			});
			if (error || !data) {
				crossoverStatus = 'error';
				return;
			}
			crossovers = data.crossovers;
			crossoverStatus = 'ready';
		} catch {
			crossoverStatus = 'error';
		}
	}

	let radioSummary = $derived(summarizeRadioCorpus(radioClips.map((row) => row.view)));

	async function loadRadio(key: string, q: string): Promise<void> {
		radioStatus = 'loading';
		try {
			if (q.trim()) {
				const { data, error } = await api.GET('/radio/search', {
					params: { query: { q, session: key } }
				});
				if (error || !data) {
					radioStatus = 'error';
					return;
				}
				radioClips = data.map((row) => ({
					view: radioClipViewFromApi(row.clip),
					clipId: row.clip.clip_id ?? null
				}));
			} else {
				const { data, error } = await api.GET('/sessions/{key}/radio', {
					params: { path: { key } }
				});
				if (error || !data) {
					radioStatus = 'error';
					return;
				}
				radioClips = data.map((clip) => ({
					view: radioClipViewFromApi(clip),
					clipId: clip.clip_id ?? null
				}));
			}
			radioStatus = 'ready';
		} catch {
			radioStatus = 'error';
		}
	}

	let radioSearchTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const q = radioQuery;
		const key = sessionKey;
		if (!key || !heavyReady) return;
		clearTimeout(radioSearchTimer);
		radioSearchTimer = setTimeout(() => void loadRadio(key, q), 250);
		return () => clearTimeout(radioSearchTimer);
	});

	function onScrollSpy(): void {
		const offset = 96;
		let current: SessionSectionId = 'story';
		for (const section of SESSION_SECTIONS) {
			const el = document.getElementById(section.id);
			if (!el) continue;
			if (el.getBoundingClientRect().top - offset <= 0) current = section.id;
		}
		activeSection = current;
	}

	function onKeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		const typing =
			target?.tagName === 'INPUT' ||
			target?.tagName === 'TEXTAREA' ||
			target?.isContentEditable;
		if (typing) return;

		if (event.key === '?') {
			event.preventDefault();
			shortcutsOpen = !shortcutsOpen;
			return;
		}
		if (event.key === '/' && !event.metaKey && !event.ctrlKey) {
			event.preventDefault();
			activeSection = 'radio';
			document.getElementById('radio')?.scrollIntoView({ behavior: 'smooth' });
			radioSearchEl?.focus();
			return;
		}
		const section = SESSION_SECTIONS.find((s) => s.shortcut === event.key);
		if (section) {
			event.preventDefault();
			document.getElementById(section.id)?.scrollIntoView({ behavior: 'smooth' });
			history.replaceState(null, '', `#${section.id}`);
			activeSection = section.id;
		}
	}

	onMount(() => {
		void load();
		driversOpen = window.matchMedia('(min-width: 1025px)').matches;
		window.addEventListener('scroll', onScrollSpy, { passive: true });
		window.addEventListener('keydown', onKeydown);
	});

	onDestroy(() => {
		disconnectStream();
		if (typeof window === 'undefined') return;
		window.removeEventListener('scroll', onScrollSpy);
		window.removeEventListener('keydown', onKeydown);
	});
</script>

<svelte:head>
	<title>
		{session?.event_name ?? `R${round}`} · {brand.name}
	</title>
</svelte:head>

{#if status === 'loading'}
	<LoadingState label="Loading session…" />
{:else if status === 'error' || !session || !sessionKey}
	<Panel title="Session not found">
		<p>{errorMessage}</p>
		<p><a href={resolve('/sessions')}>Back to sessions</a></p>
	</Panel>
{:else}
	<div class="wide-dashboard session-page">
		<aside class="drivers" aria-label="Driver selection">
			<details bind:open={driversOpen}>
				<summary>
					<span class="summary-title">Drivers</span>
					<span class="summary-count tabular">{selectedDrivers.length} selected</span>
				</summary>
				{#if classification.length === 0}
					<p class="muted">Classification loads with the session detail…</p>
				{:else}
					<ul>
						{#each classification as row (row.driver)}
							<li>
								<label>
									<input
										type="checkbox"
										checked={selectedDrivers.includes(row.driver)}
										onchange={() => toggleDriver(row.driver)}
									/>
									<span class="pos tabular">{row.position ?? '—'}</span>
									<span class="code tabular">{row.driver}</span>
								</label>
							</li>
						{/each}
					</ul>
				{/if}
			</details>
		</aside>

		<div class="main-col">
			<header class="session-header">
				<p class="eyebrow tabular">
					{session.session_key.year} · R{session.session_key.round} ·
					{session.session_key.session_type}
				</p>
				<h1>{session.event_name ?? session.key}</h1>
				{#if session.circuit_name}
					<p class="circuit">{session.circuit_name}</p>
				{/if}
			</header>

			<Panel title="Playback clock" subtitle="Replaying this session's own event stream — pace and braking sections reveal as it plays, not all at once">
				<div class="clock-row">
					<label class="speed-field">
						<span>Replay speed</span>
						<select bind:value={selectedSpeed} onchange={onSpeedChange}>
							{#each SPEED_OPTIONS as opt (opt.value)}
								<option value={opt.value}>{opt.label}</option>
							{/each}
						</select>
					</label>
					<DelaySlider {controller} />
					<DelayIndicator {controller} />
				</div>
				<TapToSync
					{controller}
					anchorEventTimeUtc={firstEventTimeUtc}
					label="Tap on this session's first event"
				/>
			</Panel>

			<SessionSubNav active={activeSection} />

			<section id="story" class="section">
				<h2>Race story</h2>
				<p class="lede">Cited narrative of the moments that mattered — the differentiator.</p>
				{#if storyStatus === 'loading'}
					<SectionSkeleton height="16rem" label="Loading race story…" />
				{:else if storyStatus === 'error'}
					<p class="muted">Could not load the story for this session. Check back after ingest.</p>
				{:else if moments.length === 0}
					<p class="muted">No anomalies cleared the bar for this race — quiet session, or thin data.</p>
				{:else}
					<ol class="story-list">
						{#each moments as moment (moment.anomalyId)}
							<li>
								{#if moment.explanation}
									<ExplanationCard explanation={moment.explanation} />
								{:else}
									<Panel title={moment.kind}>
										<p class="muted">Lap {moment.lapNumber} · no grounded explanation yet.</p>
									</Panel>
								{/if}
							</li>
						{/each}
					</ol>
				{/if}
			</section>

			<section id="shape" class="section">
				<h2>Race shape</h2>
				<p class="lede">Position and gap-to-leader over laps, for the drivers selected on the left.</p>
				{#if !heavyReady || lapsStatus === 'loading'}
					<SectionSkeleton height="14rem" label="Loading race shape…" />
				{:else if lapsStatus === 'error'}
					<Panel>
						<p class="muted">Could not load lap data for this session.</p>
					</Panel>
				{:else if selectedDrivers.length === 0}
					<Panel>
						<p class="muted">Select at least one driver to see the race shape.</p>
					</Panel>
				{:else}
					<Panel>
						<RaceShapeChart {laps} drivers={selectedDrivers} />
					</Panel>
				{/if}
			</section>

			<section id="pace" class="section">
				<h2>Pace and degradation</h2>
				<p class="lede">Fuel-corrected pace and deg curves by compound, with reliability bands.</p>
				{#if heavyReady}
					<DegradationCurves apiKey={sessionKey} {controller} />
				{:else}
					<SectionSkeleton height="14rem" />
				{/if}
			</section>

			<section id="strategy" class="section">
				<h2>Strategy</h2>
				<p class="lede">Stints, stops, and the undercut/overcut retrospective.</p>
				{#if !heavyReady || lapsStatus === 'loading'}
					<SectionSkeleton height="12rem" />
				{:else if lapsStatus === 'error'}
					<Panel>
						<p class="muted">Could not load stint data for this session.</p>
					</Panel>
				{:else if selectedDrivers.length === 0}
					<Panel>
						<p class="muted">Select at least one driver to see stint strategy.</p>
					</Panel>
				{:else}
					<Panel title="Stints and pit stops">
						<StrategyBoard {laps} drivers={selectedDrivers} />
					</Panel>
				{/if}

				<Panel
					title="Undercut / overcut retrospective"
					subtitle="The kernel's pace-projection read of each pit-window duel in this race"
				>
					{#if !heavyReady || crossoverStatus === 'loading'}
						<SectionSkeleton height="8rem" />
					{:else if crossoverStatus === 'error'}
						<p class="muted">Could not load the crossover analysis for this session.</p>
					{:else}
						<CrossoverRetrospective {crossovers} />
					{/if}
				</Panel>
			</section>

			<section id="radio" class="section">
				<h2>Radio</h2>
				<p class="lede">Searchable transcripts for this session — text only, linked to lap context.</p>
				<label class="search">
					<span class="visually-hidden">Search radio</span>
					<input
						bind:this={radioSearchEl}
						type="search"
						placeholder="Search transcripts…"
						bind:value={radioQuery}
					/>
				</label>
				{#if !heavyReady || radioStatus === 'loading'}
					<SectionSkeleton height="10rem" />
				{:else if radioStatus === 'error'}
					<p class="muted">Radio corpus unavailable for this session.</p>
				{:else if radioClips.length === 0 && radioQuery.trim() !== ''}
					<p class="muted">No transcripts match "{radioQuery.trim()}" for this session.</p>
				{:else if radioClips.length === 0}
					<p class="muted">
						No radio clips captured for this session yet — check back after ingest.
					</p>
				{:else if radioQuery.trim() === '' && radioSummary.transcribed === 0}
					<Panel>
						<p class="muted">
							{radioSummary.total} radio clip{radioSummary.total === 1 ? '' : 's'} captured for
							this weekend — none transcribed yet. Transcription runs offline in batches after
							each session; check back later, or
							<button
								type="button"
								class="link-button"
								onclick={() => (showPendingClips = !showPendingClips)}
							>
								{showPendingClips ? 'hide' : 'see'} the raw clip list
							</button>
							in the meantime.
						</p>
					</Panel>
					{#if showPendingClips}
						<ul class="radio-list">
							{#each radioClips as row, i (row.clipId ?? `${row.view.driver}-${i}`)}
								<li>
									<RadioClipCard clip={row.view} clipId={row.clipId} allowAudio={false} />
								</li>
							{/each}
						</ul>
					{/if}
				{:else}
					{#if radioQuery.trim() === '' && radioSummary.transcribed < radioSummary.total}
						<p class="corpus-note">
							{radioSummary.transcribed} of {radioSummary.total} clips transcribed so far.
						</p>
					{/if}
					<ul class="radio-list">
						{#each radioClips as row, i (row.clipId ?? `${row.view.driver}-${i}`)}
							<li>
								<RadioClipCard clip={row.view} clipId={row.clipId} allowAudio={false} />
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section id="braking" class="section">
				<h2>Braking drift</h2>
				<p class="lede">Corner approach trend as tyres age — kernel output, not vision.</p>
				{#if heavyReady}
					<BrakingPointDrift
						apiKey={sessionKey}
						{controller}
						drivers={selectedDrivers.length ? selectedDrivers : classification.map((c) => c.driver)}
					/>
				{:else}
					<SectionSkeleton height="12rem" />
				{/if}
			</section>

			<section id="method" class="section">
				<h2>Method</h2>
				<p class="lede">How these numbers were produced, and what they cannot tell you.</p>
				<Panel>
					<ul class="method-list">
						<li>
							Pace and degradation use fuel-corrected clean laps only. Dirty air, safety cars, and
							in/out laps are excluded and reported — nothing is silently dropped.
						</li>
						<li>
							Every derived value carries a reliability band. Below four clean laps we show no
							number at all.
						</li>
						<li>
							Historical backfill never includes live car positions. Track maps for those sessions
							show the outline only.
						</li>
						<li>
							Radio transcripts are derived offline with confidence scores. Low-confidence text is
							labeled, never presented as certain.
						</li>
						<li>
							{brand.name} is unofficial and not associated with Formula One Licensing B.V.
						</li>
					</ul>
				</Panel>
			</section>
		</div>
	</div>

	<ShortcutSheet open={shortcutsOpen} onClose={() => (shortcutsOpen = false)} />
{/if}

<style>
	.session-page {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-5);
	}

	@media (min-width: 1025px) {
		.session-page {
			grid-template-columns: 12rem minmax(0, 1fr);
			align-items: start;
		}
	}

	.drivers {
		border: 1px solid var(--hairline);
		background: var(--surface-raised);
		padding: var(--space-4);
	}

	@media (min-width: 1025px) {
		.drivers {
			position: sticky;
			top: var(--space-4);
			max-height: calc(100dvh - 2rem);
			overflow: auto;
		}
	}

	.drivers summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		font-size: var(--font-size-sm);
		color: var(--text-secondary);
		cursor: pointer;
		list-style: none;
	}

	.drivers summary::-webkit-details-marker {
		display: none;
	}

	.drivers details[open] summary {
		margin-bottom: var(--space-3);
	}

	.summary-title {
		font-weight: 600;
		color: var(--text-primary);
	}

	.summary-count {
		color: var(--text-muted);
		font-size: var(--font-size-xs);
	}

	.drivers ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}

	.drivers label {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--font-size-sm);
		cursor: pointer;
		color: var(--text-secondary);
	}

	.pos {
		width: 1.5rem;
		color: var(--text-muted);
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}

	.code {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
		color: var(--text-primary);
	}

	.session-header h1 {
		margin-bottom: var(--space-2);
	}

	.clock-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-4);
	}

	.speed-field {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.speed-field select {
		padding: var(--space-1) var(--space-2);
		border: 1px solid var(--hairline-strong);
		background: var(--surface-base);
		color: var(--text-primary);
		font: inherit;
	}

	.eyebrow {
		margin: 0 0 var(--space-2);
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.circuit {
		margin: 0 0 var(--space-2);
		color: var(--text-secondary);
	}

	.section {
		padding-top: var(--space-6);
		scroll-margin-top: 3.5rem;
	}

	.section h2 {
		margin-bottom: var(--space-2);
	}

	.lede {
		margin-bottom: var(--space-4);
		font-size: var(--font-size-sm);
		color: var(--text-muted);
	}

	.story-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.radio-list {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.search input {
		width: 100%;
		max-width: 28rem;
		padding: var(--space-2) var(--space-3);
		border: 1px solid var(--hairline-strong);
		background: var(--surface-raised);
		color: var(--text-primary);
		font: inherit;
		margin-bottom: var(--space-3);
	}

	.method-list {
		margin: 0;
		padding-left: var(--space-5);
		color: var(--text-secondary);
		font-size: var(--font-size-sm);
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.muted {
		color: var(--text-muted);
		font-size: var(--font-size-sm);
	}

	.link-button {
		background: none;
		border: none;
		padding: 0;
		font: inherit;
		color: var(--text-primary);
		text-decoration: underline;
		text-underline-offset: 2px;
		cursor: pointer;
	}

	.corpus-note {
		margin: 0 0 var(--space-3);
		font-size: var(--font-size-xs);
		color: var(--text-muted);
	}

	.tabular {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
</style>
