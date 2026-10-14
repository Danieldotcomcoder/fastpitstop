<!--
  The race desk, shared by `/sessions/[year]/[round]` (a replay) and `/live`
  (a session in progress): the supplied design (pitwall-race-dashboard.html)
  on real data.

  Both routes render THIS component, so the two are the same product by
  construction. The only differences are the ones that genuinely exist: a
  live session has no playback speed to pick and nothing to restart, and
  `/live` hides the back-link to the session list.

  Everything on screen comes through one Delay Sync controller: the stream
  is ingested into it, and every panel reads only what it has released, so
  nothing ahead of the viewer's clock can reach the page. Whole-session REST
  reads (`/laps`, braking) are further gated by the reveal tracker.
-->
<script lang="ts">
	import '$lib/styles/desk.css';
	import { onDestroy, untrack } from 'svelte';
	import { resolve } from '$app/paths';
	import { api, type components } from '$lib/api/client';
	import { SessionStream } from '$lib/api/stream';
	import {
		DelaySyncController,
		EMPTY_VISIBLE_STATE,
		measureServerOffset,
		playbackClockMs,
		serverNowMs,
		setServerClockOffsetMs,
		type VisibleSessionState
	} from '$lib/delay-sync';
	import QualifyingBoard from '$lib/qualifying/QualifyingBoard.svelte';
	import { QualifyingReducer, qualiView } from '$lib/qualifying/reducer';
	import { RevealTracker } from '$lib/kernel-views';
	import { namesFromEvents } from '$lib/driver-events';
	import { driverName } from '$lib/drivers';
	import { raceLap } from '$lib/race-lap';
	import { TimingTowerReducer } from '$lib/timing-tower';
	import { ExplanationLaneReducer } from '$lib/why';
	import DeskAnalytics from '$lib/desk/DeskAnalytics.svelte';
	import DeskIcons from '$lib/desk/DeskIcons.svelte';
	import DeskMap, { type DeskCircuit } from '$lib/desk/DeskMap.svelte';
	import DeskTooltip from '$lib/desk/DeskTooltip.svelte';
	import DeskTower from '$lib/desk/DeskTower.svelte';
	import FeedLane from '$lib/desk/FeedLane.svelte';
	import PhoneGlance from '$lib/desk/PhoneGlance.svelte';
	import PitBoard from '$lib/desk/PitBoard.svelte';
	import SyncStrip from '$lib/desk/SyncStrip.svelte';
	import WhyLane from '$lib/desk/WhyLane.svelte';
	import { focusPair } from '$lib/desk/focus';
	import { LapRecordReducer } from '$lib/desk/lap-records';
	import { buildBoard, crossoverFor, type Crossover } from '$lib/desk/pit-board';
	import { RaceFeedReducer, trackStatusChip } from '$lib/desk/race-feed';
	import { SnapshotHistory } from '$lib/desk/snapshot-history';
	import { latestWeather, weatherReadings } from '$lib/desk/weather';
	import BottomNav from '$lib/ui/BottomNav.svelte';

	type Session = components['schemas']['SessionSummaryOut'];
	type Lap = components['schemas']['LapRowOut'];

	interface Props {
		/** The session to drive, already resolved by the route. */
		session: Session;
		/** Show the back-link to the session list. Off for `/live`. */
		showBackLink?: boolean;
		/** The weekend's sessions, for links between them (replays only). */
		weekend?: Session[];
	}

	let { session, showBackLink = true, weekend = [] }: Props = $props();

	/** `live`, never `provenance`: `provenance === 'live'` only means the
	 * store serves this session, which stays true forever once ingested —
	 * reading it opened a `mode=live` stream against a race that had
	 * finished six days earlier. */
	let isLive = $derived(session.live === true);

	const SPEEDS = [0, 1, 4, 5, 16, 20, 50];
	let speed = $state(16);
	let controller = $state(new DelaySyncController({ initialDelaySeconds: 0, now: serverNowMs }));
	// Raw, not deep: the released state is replaced, never mutated, and a
	// deep proxy made every reducer's read of every event a proxy trap
	// (16% of CPU at 16x on the live-recorded 2026 race, frames dropped).
	let visible = $state.raw<VisibleSessionState>(EMPTY_VISIBLE_STATE);
	let connection = $state('Connecting');
	let lightsOutUtc = $state<string | null>(null);
	let firstEventUtc = $state<string | null>(null);
	let laps = $state<Lap[]>([]);
	let circuit = $state<DeskCircuit | null>(null);
	let circuitStatus = $state<'loading' | 'ready' | 'missing'>('loading');
	let focus = $state<string | null>(null);
	let feedFilter = $state<'all' | 'radio' | 'rc'>('all');

	/** The phone's tabs (ROADMAP W7): below 900px the desk shows one view
	 * at a time, picked from the bottom bar; wider screens show them all
	 * and ignore this. */
	const PHONE_TABS = [
		{ id: 'race', label: 'Race', icon: 'M4 6h16M4 12h16M4 18h10' },
		{ id: 'battle', label: 'Battle', icon: 'M7 17 17 7M9 7h8v8' },
		{
			id: 'map',
			label: 'Map',
			icon: 'M7 4c3 0 3 4 6 4s3-4 6-2v10c0 3-3 4-5 2s-3-2-5 0-6 1-6-2V7c0-2 2-3 4-3z'
		},
		{ id: 'strategy', label: 'Strategy', icon: 'M4 20h16M7 16v-5M12 16V7M17 16v-3' },
		{ id: 'story', label: 'Story', icon: 'M6 4h12v16l-6-4-6 4z' }
	] as const;
	let phoneTab = $state<string>('race');
	let deskEl: HTMLElement | undefined = $state();

	/** On a phone a tab's panels are mounted the first time it is opened, and
	 * then kept (so the tower's Int/Gap choice or the map survive a switch).
	 * Mounting every panel up front, hidden, cost the phone's first paint
	 * the whole desk's work (ROADMAP W7's load-time target). */
	const PHONE_QUERY = '(max-width: 899px)';
	let isPhone = $state(typeof window !== 'undefined' && window.matchMedia(PHONE_QUERY).matches);
	let opened = $state<Record<string, true>>({ race: true });
	const shows = (tab: string) => !isPhone || opened[tab] === true;
	$effect(() => {
		const query = window.matchMedia(PHONE_QUERY);
		const update = () => (isPhone = query.matches);
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});

	function selectTab(id: string): void {
		opened[id] = true;
		phoneTab = id;
		// A new tab starts at its top, not wherever the last one was scrolled.
		if (deskEl && deskEl.getBoundingClientRect().top < 0) {
			deskEl.scrollIntoView({ block: 'start' });
		}
	}

	let socket: SessionStream | null = null;
	let timer: ReturnType<typeof setInterval> | null = null;
	let generation = 0;

	const tower = new TimingTowerReducer();
	const reveal = new RevealTracker();
	const records = new LapRecordReducer();
	const why = new ExplanationLaneReducer();
	const feed = new RaceFeedReducer();
	const history = new SnapshotHistory();
	const quali = new QualifyingReducer();

	/** Qualifying and sprint qualifying get the qualifying board in place of
	 * the race tower, pit board and race analytics. */
	let isQualifying = $derived(
		session.session_key.session_type === 'qualifying' ||
			session.session_key.session_type === 'sprint_qualifying'
	);
	/** The viewer's playback time, for the qualifying clock between updates. */
	let playbackMs = $state(0);

	const year = $derived(session.session_key.year);
	$effect(() => controller.visibleState.subscribe((s) => (visible = s)));

	// --- folds over the released log (each reducer is incremental) ---------
	let rows = $derived.by(() => {
		tower.update(visible.events);
		records.update(visible.events);
		reveal.update(visible.events);
		why.update(visible.events);
		feed.update(visible.events);
		if (isQualifying) quali.update(visible.events);
		// Fresh copies: the reducer mutates rows in place, and Svelte only
		// re-renders a keyed row whose value changed identity.
		return tower.rows.map((r) => ({ ...r }));
	});
	/** Bumps whenever the released log grows, for views that read reducers
	 * through a function rather than a value. */
	let revision = $derived(visible.events.length);

	let names = $derived.by(() => {
		const fromEvents = namesFromEvents(visible.events);
		const out: Record<string, string> = { ...fromEvents };
		for (const r of rows) out[r.driver] = driverName(r.driver, r.name, r.code, year);
		return out;
	});
	let codes = $derived(Object.fromEntries(rows.map((r) => [r.driver, r.code ?? r.driver])));
	let teams = $derived(Object.fromEntries(rows.map((r) => [r.driver, r.team])));

	let completedLaps = $derived(Math.max(0, ...rows.map((r) => r.lapNumber ?? 0)));
	let lap = $derived(raceLap(visible.events, completedLaps));
	let qualiBoard = $derived.by(() => {
		void rows;
		if (!isQualifying) return null;
		return qualiView(quali.state, playbackMs, {
			sprint: session.session_key.session_type === 'sprint_qualifying',
			timeScale: isLive ? 1 : speed > 0 ? speed : 1
		});
	});
	let qualiOver = $derived(
		qualiBoard !== null && qualiBoard.part === 3 && qualiBoard.clockState === 'over'
	);
	let finished = $derived.by(() => {
		void revision;
		// Qualifying shows a chequered flag at the end of every part: it is
		// over only when its last part is.
		if (isQualifying) return qualiOver;
		return reveal.state.sessionFinished;
	});
	let totalLaps = $derived(lap.total ?? (finished ? completedLaps : null));
	let status = $derived(trackStatusChip(visible.events, finished));

	/** Conditions as of the viewer's clock (W3's weather strip). */
	let weather = $derived.by(() => {
		void revision;
		return weatherReadings(latestWeather(visible.events));
	});

	let revealedLaps = $derived.by(() => {
		void revision;
		return laps.filter((l) => reveal.isLapRevealed(l.driver, l.lap_number));
	});

	// --- the focused battle --------------------------------------------------
	function isCrossover(value: unknown): value is Crossover {
		if (typeof value !== 'object' || value === null) return false;
		const v = value as Record<string, unknown>;
		return (
			typeof v.attacker === 'string' &&
			typeof v.defender === 'string' &&
			typeof v.kind === 'string' &&
			typeof v.window === 'string'
		);
	}
	let crossovers = $derived((visible.latestSnapshot?.crossovers ?? []).filter(isCrossover));
	$effect(() => history.record(visible.latestSnapshot, isCrossover));

	let pair = $derived(focusPair(rows, focus));
	let crossover = $derived(pair ? crossoverFor(crossovers, pair) : null);
	let board = $derived(buildBoard(rows, pair, crossover, codes));
	let spark = $derived.by(() => {
		void visible.latestSnapshot;
		return pair ? history.points(pair.a, pair.b) : [];
	});
	let explanations = $derived.by(() => {
		void revision;
		return why.explanations;
	});
	let feedItems = $derived.by(() => {
		void revision;
		return feed.list(feedFilter);
	});

	const mmss = (s: number) =>
		`${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
	let qualiFlag = $derived.by(() => {
		const b = qualiBoard;
		if (!b || b.part === null) return connection === 'Receiving' ? 'Waiting for Q1' : connection;
		if (b.clockState === 'running') return 'To go';
		if (b.clockState === 'stopped') return 'Clock stopped';
		if (b.clockState === 'over') return b.part === 3 ? 'Qualifying over' : `${b.label} over`;
		return 'Waiting';
	});

	function lastLapClass(driver: string, lapTime: number | null): '' | 'fastest' | 'pb' {
		void revision;
		return records.classify(driver, lapTime);
	}
	function sectorClass(driver: string, index: number, time: number | null): '' | 'fastest' | 'pb' {
		void revision;
		return records.classifySector(driver, index, time);
	}

	// --- the clock -------------------------------------------------------------
	/** Delay Sync compares the viewer's clock with the server's timestamps,
	 * so it runs on the device clock corrected to the server's
	 * (`$lib/delay-sync/server-clock`). Nothing is released until the
	 * correction is known: a device clock running fast would otherwise
	 * release the first moments early, a spoiler for anyone on a delay. */
	let clockKnown = false;
	/** The correction in use, for the release gate's readout below. */
	let clockOffsetMs = $state(0);

	async function syncClock(): Promise<void> {
		try {
			const offset = await measureServerOffset(async () => {
				const { data } = await api.GET('/time', { signal: AbortSignal.timeout(2_000) });
				if (data === undefined) throw new Error('no server time');
				return data.server_time_ms;
			});
			setServerClockOffsetMs(offset);
			clockOffsetMs = offset;
		} finally {
			// Measured, or every attempt failed (offset 0, the uncorrected
			// behaviour): either way the dashboard runs.
			clockKnown = true;
		}
	}
	void syncClock();

	// --- the stream ----------------------------------------------------------
	/** Close codes the API uses to refuse a stream outright: a malformed key,
	 * or (for `mode=live`) a session that is no longer running. Reconnecting
	 * after either would only be refused again. */
	const REFUSED = new Set([4400, 4404]);
	const RECONNECT_MIN_MS = 1_000;
	const RECONNECT_MAX_MS = 15_000;
	let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	let reconnectDelayMs = RECONNECT_MIN_MS;

	function disconnect() {
		socket?.close();
		socket = null;
		if (timer !== null) clearInterval(timer);
		timer = null;
		if (reconnectTimer !== null) clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}

	function connect(delay = controller.delayState.get().delaySeconds) {
		disconnect();
		for (const reducer of [tower, reveal, records, why, feed, history, quali]) reducer.reset();
		visible = EMPTY_VISIBLE_STATE;
		lightsOutUtc = null;
		firstEventUtc = null;
		const c = new DelaySyncController({ initialDelaySeconds: delay, now: serverNowMs });
		controller = c;
		connection = 'Connecting';
		const stream = new SessionStream(session.key, { mode: isLive ? 'live' : 'replay', speed });
		stream.onMessage((e) => {
			// Tap-to-sync needs the instant the lights went out: the race's own
			// start. Read off the incoming stream (its timestamp only, never its
			// content) so it is known even while the event is still held back.
			// A backfilled race carries no start status; there the stream's
			// first event stands in, as it always did before.
			firstEventUtc ??= e.event_time_utc;
			if (
				lightsOutUtc === null &&
				e.kind === 'session_status_changed' &&
				e.payload.status === 'Started'
			) {
				lightsOutUtc = e.event_time_utc;
			}
			connection = 'Receiving';
			reconnectDelayMs = RECONNECT_MIN_MS;
			c.ingest(e);
		});
		stream.onError(() => (connection = 'Connection interrupted'));
		stream.onClose((event) => {
			if (socket !== stream) return;
			// A live race has to survive a dropped connection: a phone
			// switching networks, or the API restarting mid-session. Before
			// this, one blip left the dashboard frozen on "Stream ended" for
			// the rest of the race. The live stream resends the session from
			// its start, so reconnecting resyncs everything (connect() resets
			// the reducers) and Delay Sync keeps the viewer's own delay. A
			// replay's stream ends by design, and a refusal is final.
			if (isLive && !REFUSED.has(event.code)) {
				connection = 'Reconnecting';
				const wait = reconnectDelayMs;
				reconnectDelayMs = Math.min(reconnectDelayMs * 2, RECONNECT_MAX_MS);
				reconnectTimer = setTimeout(() => {
					reconnectTimer = null;
					if (socket === stream) connect();
				}, wait);
				return;
			}
			connection = isLive ? 'Session ended' : 'Stream ended';
		});
		socket = stream;
		stream.connect();
		// Ten releases a second: the page moves as the race does.
		timer = setInterval(() => {
			if (!clockKnown) return;
			c.tick();
			if (isQualifying) {
				playbackMs = playbackClockMs(serverNowMs(), c.delayState.get().delaySeconds);
			}
		}, 100);
	}

	async function loadLaps(key: string): Promise<void> {
		const own = generation;
		laps = [];
		try {
			const response = await api.GET('/sessions/{key}/laps', { params: { path: { key } } });
			if (own === generation) laps = response.data ?? [];
		} catch {
			// The live panels carry on without the lap history.
		}
	}

	async function loadCircuit(key: string): Promise<void> {
		const own = generation;
		circuit = null;
		circuitStatus = 'loading';
		try {
			const { data } = await api.GET('/sessions/{key}/circuit', { params: { path: { key } } });
			if (own !== generation) return;
			circuit = data ?? null;
			circuitStatus = data ? 'ready' : 'missing';
		} catch {
			if (own === generation) circuitStatus = 'missing';
		}
	}

	function readUrlOptions(): number {
		if (typeof window === 'undefined') return 0;
		const params = new URLSearchParams(window.location.search);
		const s = Number(params.get('speed'));
		if (params.has('speed') && SPEEDS.includes(s)) speed = s;
		const d = Number(params.get('delay'));
		return params.has('delay') && Number.isFinite(d) ? d : 0;
	}

	function setSpeed(next: number): void {
		speed = next;
		connect();
	}

	// One effect per session, keyed on `session.key`, so a `/live` session
	// change tears the old stream down rather than leaking both. `untrack`
	// because connect() both reads and writes `speed`.
	$effect(() => {
		const key = session.key;
		untrack(() => {
			generation++;
			focus = null;
			connect(readUrlOptions());
			void loadLaps(key);
			void loadCircuit(key);
		});
		return () => disconnect();
	});

	onDestroy(() => {
		generation++;
		disconnect();
	});

	const TYPE_LABEL: Record<string, string> = {
		race: 'Race',
		sprint: 'Sprint',
		qualifying: 'Qualifying',
		sprint_qualifying: 'Sprint qualifying',
		practice_1: 'Practice 1',
		practice_2: 'Practice 2',
		practice_3: 'Practice 3'
	};
	/** Links to the weekend's other competitive sessions, in running order. */
	const LINK_ORDER = ['sprint_qualifying', 'sprint', 'qualifying', 'race'];
	let weekendLinks = $derived(
		LINK_ORDER.flatMap((type) => {
			const s = weekend.find((w) => w.session_key.session_type === type);
			if (!s) return [];
			const path = resolve(`/sessions/${s.session_key.year}/${s.session_key.round}`);
			return [
				{
					type,
					label: TYPE_LABEL[type] ?? type,
					href: type === 'race' ? path : `${path}?session=${type}`,
					current: s.key === session.key
				}
			];
		})
	);
	let meta = $derived(
		[
			TYPE_LABEL[session.session_key.session_type] ?? session.session_key.session_type,
			totalLaps !== null && !isQualifying ? `${totalLaps} laps` : null,
			isLive ? 'Live' : `Replay · ${year}`
		]
			.filter(Boolean)
			.join(' · ')
	);
	let leaderCode = $derived.by(() => {
		const leader = rows.find((r) => r.position === 1);
		return leader ? (codes[leader.driver] ?? leader.driver) : null;
	});
	let lapsToGo = $derived.by(() => {
		if (finished) return 'Chequered flag';
		if (lap.current === 0) return connection === 'Receiving' ? 'Waiting for the start' : connection;
		// A replay of a backfilled race does not announce its distance, and
		// the finishing lap count would give away a shortened race. So the
		// line says who leads instead of guessing how many laps remain.
		if (totalLaps === null) return leaderCode ? `${leaderCode} leads` : connection;
		const togo = totalLaps - lap.current;
		return togo <= 0 ? 'Final lap' : `${togo} lap${togo === 1 ? '' : 's'} to go`;
	});
</script>

<div class="wide-dashboard desk-page" data-tab={qualiBoard ? undefined : phoneTab}>
	<DeskIcons />
	<header class="masthead">
		<div class="brand">
			{#if showBackLink}<a class="back-link" href={resolve('/sessions')}>← Sessions</a>{/if}
			<h1 class="session-title">{session.event_name ?? session.key}</h1>
			<div class="session-meta">{meta}</div>
			{#if weekendLinks.length > 1}
				<nav class="weekend-links" aria-label="Sessions this weekend">
					{#each weekendLinks as link (link.type)}
						<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
						<a href={link.href} aria-current={link.current ? 'page' : undefined}>{link.label}</a>
					{/each}
				</nav>
			{/if}
			<dl class="weather" data-testid="weather" aria-label="Weather as of your clock">
				{#each weather as reading (reading.label)}
					<div>
						<dt>{reading.label}</dt>
						<dd class="num">{reading.value}</dd>
					</div>
				{/each}
			</dl>
		</div>
		{#if qualiBoard}
			<div class="lapcount" data-testid="quali-clock">
				<span class="caps">{qualiBoard.label ?? 'Qualifying'}</span>
				<span class="big num">{qualiBoard.clockS !== null ? mmss(qualiBoard.clockS) : '—'}</span>
				<span class="flag">{qualiFlag}</span>
			</div>
		{:else}
			<div class="lapcount" aria-live="polite">
				<span class="caps">{finished ? 'Finished' : 'Lap'}</span>
				<span class="big" data-testid="session-lap">{String(lap.current).padStart(2, '0')}</span
				>{#if totalLaps !== null}<span class="of">/{totalLaps}</span>{/if}
				<span class="flag">{lapsToGo}</span>
			</div>
		{/if}
	</header>

	<SyncStrip
		{controller}
		{isLive}
		{speed}
		{status}
		lightsOutUtc={lightsOutUtc ?? firstEventUtc}
		onSpeed={setSpeed}
		onRestart={() => connect()}
	/>

	{#if qualiBoard}
		<main class="desk q-desk">
			<QualifyingBoard view={qualiBoard} {names} />
			<DeskMap
				{controller}
				{circuit}
				{circuitStatus}
				{rows}
				pair={null}
				{codes}
				timeScale={isLive ? 1 : speed > 0 ? speed : 1}
				estimate={false}
			/>
			<FeedLane
				items={feedItems}
				filter={feedFilter}
				{codes}
				{teams}
				onFilter={(f) => (feedFilter = f)}
			/>
		</main>
	{:else}
		<main class="desk" bind:this={deskEl}>
			{#if isPhone}
				<PhoneGlance
					{rows}
					model={board}
					{spark}
					{names}
					{explanations}
					onFocus={(d) => (focus = d)}
					onAll={() => selectTab('strategy')}
				/>
			{/if}
			{#if shows('strategy')}
				<DeskTower
					{rows}
					{focus}
					{pair}
					{names}
					currentLap={lap.current}
					{totalLaps}
					{lastLapClass}
					{sectorClass}
					onFocus={(d) => (focus = d)}
				/>
			{/if}
			{#if shows('story')}
				<WhyLane {explanations} {codes} {teams} />
			{/if}
			{#if shows('map')}
				<DeskMap
					{controller}
					{circuit}
					{circuitStatus}
					{rows}
					{pair}
					{codes}
					timeScale={isLive ? 1 : speed > 0 ? speed : 1}
				/>
			{/if}
			{#if shows('battle')}
				<PitBoard
					model={board}
					{spark}
					focusName={focus ? (names[focus] ?? null) : pair ? (names[pair.a] ?? null) : null}
				/>
			{/if}
			{#if shows('strategy')}
				<DeskAnalytics
					apiKey={session.key}
					laps={revealedLaps}
					{pair}
					{crossover}
					{codes}
					{names}
					{teams}
					{totalLaps}
					isStintRevealed={(driver, stint) => reveal.isStintRevealed(driver, stint)}
					{revision}
				/>
			{/if}
			{#if shows('story')}
				<FeedLane
					items={feedItems}
					filter={feedFilter}
					{codes}
					{teams}
					onFilter={(f) => (feedFilter = f)}
				/>
			{/if}
		</main>
	{/if}

	{#if !qualiBoard}
		<div class="phone-nav">
			<BottomNav items={PHONE_TABS} selected={phoneTab} label="Race views" onSelect={selectTab} />
		</div>
	{/if}

	<footer class="site-foot">
		<span>{finished ? 'Race complete' : connection} · {rows.length} drivers on the timing feed</span
		>
		<span>Showing only the race you’ve reached.</span>
	</footer>
	<DeskTooltip />
	<span hidden data-testid="session-visible-events">{visible.events.length}</span>
	<span hidden data-testid="session-last-event">{visible.lastEventTimeUtc ?? ''}</span>
	<span hidden data-testid="session-clock-offset">{clockOffsetMs}</span>
</div>

<style>
	.back-link {
		font-size: 12px;
		color: var(--muted);
		text-decoration: none;
		margin-bottom: 2px;
	}
	.back-link:hover {
		color: var(--ink);
	}
	.weekend-links {
		display: flex;
		flex-wrap: wrap;
		gap: 4px 14px;
		margin-top: 2px;
		font-size: 12.5px;
	}
	.weekend-links a {
		color: var(--muted);
		text-decoration: none;
		padding-bottom: 1px;
		border-bottom: 1px solid transparent;
	}
	.weekend-links a:hover {
		color: var(--ink);
	}
	.weekend-links a[aria-current='page'] {
		color: var(--ink);
		border-bottom-color: var(--ink);
	}
</style>
