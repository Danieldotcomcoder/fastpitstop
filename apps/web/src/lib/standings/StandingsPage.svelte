<!--
  The championship standings (ROADMAP M2, mockup A), behind their own
  spoiler shield: points count only up to the last race the viewer says
  they've watched. Until they say, a race from the past week is left out.
  The cutoff lives in this browser, so the server renders the page's frame
  and the standings are fetched here, already cut off: no later race's
  points ever reach the device.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { api } from '$lib/api/client';
	import { serverNowMs } from '$lib/delay-sync';
	import { Sparkline } from '$lib/ui';
	import {
		defaultCutoff,
		hiddenRounds,
		leaderSentence,
		progressionChart,
		readWatched,
		storeWatched,
		type StandingsOut
	} from './view';

	interface Props {
		season: number;
		/** Seasons to offer, newest first. */
		seasons: readonly number[];
	}

	let { season, seasons }: Props = $props();

	let status = $state<'loading' | 'ready' | 'error'>('loading');
	let data = $state<StandingsOut | null>(null);
	let cutoff = $state<number | null>(null);
	let chosen = $state(false);
	let showAll = $state(false);

	const storage = () => {
		try {
			return window.localStorage;
		} catch {
			return null;
		}
	};

	async function fetchStandings(afterRound: number): Promise<StandingsOut | null> {
		const { data: body, error } = await api.GET('/standings', {
			params: { query: { season, after_round: afterRound } }
		});
		return error ? null : body;
	}

	async function load(): Promise<void> {
		status = 'loading';
		try {
			const stored = readWatched(season, storage());
			chosen = stored !== null;
			let round = stored;
			if (round === null) {
				// Nothing chosen yet: ask for no points at all first, just the
				// list of rounds, and pick the default cutoff from their dates.
				const rounds = await fetchStandings(0);
				if (!rounds) throw new Error('standings');
				round = defaultCutoff(rounds.rounds, serverNowMs());
			}
			const body = await fetchStandings(round);
			if (!body) throw new Error('standings');
			cutoff = round;
			data = body;
			status = 'ready';
		} catch {
			status = 'error';
		}
	}

	async function choose(round: number): Promise<void> {
		storeWatched(season, round, storage());
		chosen = true;
		const body = await fetchStandings(round);
		if (body) {
			cutoff = round;
			data = body;
		}
	}

	onMount(() => {
		void load();
	});

	let rounds = $derived(data?.rounds ?? []);
	let counted = $derived(rounds.filter((r) => r.round <= (data?.after_round ?? 0)));
	let last = $derived(counted.at(-1) ?? null);
	let hidden = $derived(data ? hiddenRounds(rounds, data.after_round) : []);
	let latestRound = $derived(rounds.at(-1)?.round ?? 0);
	let drivers = $derived(data?.drivers ?? []);
	let visibleDrivers = $derived(showAll ? drivers : drivers.slice(0, 12));
	let leader = $derived(drivers[0]?.points ?? 0);
	let domain = $derived([0, Math.max(1, leader)] as const);
	let chart = $derived(
		progressionChart(
			drivers,
			counted.map((r) => `R${r.round}`),
			620,
			230
		)
	);
	const roundName = (r: { round: number; event_name: string | null }) =>
		`${(r.event_name ?? `Round ${r.round}`).replace(/ Grand Prix$/, '')}, round ${r.round}`;
	const pts = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));
</script>

<article class="standings wide-dashboard">
	<header>
		<div>
			<p class="kicker">{season} season</p>
			<h1>Standings</h1>
			{#if status === 'ready' && data && rounds.length}
				<p class="after" data-testid="standings-cutoff">
					{#if last}
						After <strong>{roundName(last)}</strong>
						<span class="count">· {counted.length} of {rounds.length} races counted</span>
					{:else}
						No race counted yet
					{/if}
				</p>
			{/if}
			<nav class="seasons" aria-label="Season">
				{#each seasons as year (year)}
					<a
						href={resolve('/standings/[year]', { year: String(year) })}
						aria-current={year === season ? 'page' : undefined}>{year}</a
					>
				{/each}
			</nav>
		</div>

		{#if status === 'ready' && data && rounds.length}
			<label class="change">
				<span>Count races up to</span>
				<select
					value={cutoff ?? 0}
					onchange={(e) => choose(Number((e.currentTarget as HTMLSelectElement).value))}
				>
					<option value={0}>none yet</option>
					{#each rounds as r (r.round)}
						<option value={r.round}>{roundName(r)}</option>
					{/each}
				</select>
			</label>
		{/if}
	</header>

	{#if status === 'ready' && data && hidden.length}
		<div class="notice" role="status" data-testid="standings-notice">
			<p>
				{#if chosen}
					Counting up to the last race you said you've watched.
				{:else}
					Held back in case you haven't watched it yet:
				{/if}
				<strong>{hidden.map(roundName).join(', ')}</strong>
				{hidden.length === 1 ? 'is' : 'are'} not counted.
			</p>
			<button type="button" class="count-all" onclick={() => choose(latestRound)}>
				{hidden.length === 1
					? `Count ${(hidden[0]!.event_name ?? 'it').replace(/ Grand Prix$/, '')}`
					: `Count all ${hidden.length} races`}
			</button>
		</div>
	{/if}

	{#if status === 'loading'}
		<p class="muted" aria-live="polite">Loading the standings…</p>
	{:else if status === 'error'}
		<p class="muted">The standings can't be loaded right now. Try again in a minute.</p>
	{:else if data && rounds.length === 0}
		<p class="muted">
			No {season} race has been archived yet.
			{#if seasons.includes(season - 1)}
				<a href={resolve('/standings/[year]', { year: String(season - 1) })}>See {season - 1}</a
				>{/if}
		</p>
	{:else if data && drivers.length === 0}
		<p class="muted">Choose the last race you watched to see the standings after it.</p>
	{:else if data}
		<div class="layout">
			<section class="card drivers" aria-labelledby="drivers-heading">
				<h2 id="drivers-heading">Drivers</h2>
				<ol>
					{#each visibleDrivers as d (d.code)}
						<li>
							<span class="pos num">{d.position}</span>
							<span class="who">
								<span class="bar" style="background: {d.team_color ?? 'var(--rule-strong)'}"></span>
								<span class="name">{d.name}</span>
								{#if d.team}<span class="team">{d.team}</span>{/if}
							</span>
							<span class="wins">{d.wins} {d.wins === 1 ? 'win' : 'wins'}</span>
							<span class="spark">
								<Sparkline
									values={d.by_round}
									{domain}
									width={100}
									height={22}
									color={d.team_color ?? 'var(--ink-2)'}
									label={`${d.name}'s points after each round`}
								/>
							</span>
							<span class="gap num"
								>{d.position === 1 ? 'Leader' : `−${pts(leader - d.points)}`}</span
							>
							<span class="points num">{pts(d.points)}</span>
						</li>
					{/each}
				</ol>
				<p class="foot">
					Sprint points included.
					{#if drivers.length > 12}
						<button type="button" class="link" onclick={() => (showAll = !showAll)}>
							{showAll ? 'Show the top 12' : `Show all ${drivers.length}`}
						</button>
					{/if}
				</p>
			</section>

			<div class="side">
				{#if chart}
					<section class="card" aria-labelledby="progress-heading">
						<h2 id="progress-heading">How the top six got here</h2>
						<svg
							class="chart"
							viewBox="0 0 620 230"
							role="img"
							aria-label="Points after each round, top six drivers"
						>
							{#each chart.grid as g (g.value)}
								<line x1="34" x2="550" y1={g.y} y2={g.y}></line>
								<text class="axis" x="28" y={g.y + 4} text-anchor="end">{g.value}</text>
							{/each}
							{#each chart.rounds as r (r.label)}
								<text class="axis" x={r.x} y="224" text-anchor="middle">{r.label}</text>
							{/each}
							{#each chart.lines as line (line.code)}
								<polyline
									points={line.points}
									stroke={line.color}
									style:stroke-dasharray={line.dashed ? 'var(--teammate-dash)' : null}
								></polyline>
								<text class="end" x={line.label.x} y={line.label.y} fill={line.color}
									>{line.label.text}</text
								>
							{/each}
						</svg>
						<p class="note">{leaderSentence(drivers, data.remaining)}</p>
					</section>
				{/if}

				<section class="card" aria-labelledby="teams-heading">
					<h2 id="teams-heading">Constructors</h2>
					<ol class="teams">
						{#each data.constructors as t (t.team)}
							<li>
								<span class="pos num">{t.position}</span>
								<span class="who">
									<span class="bar" style="background: {t.team_color ?? 'var(--rule-strong)'}"
									></span>
									{t.team}
								</span>
								<span class="points num">{pts(t.points)}</span>
							</li>
						{/each}
					</ol>
				</section>
			</div>
		</div>
	{/if}
</article>

<style>
	.standings {
		width: 100%;
		max-width: 1180px;
		margin: 0 auto;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	header {
		display: flex;
		flex-wrap: wrap;
		justify-content: space-between;
		align-items: flex-end;
		gap: var(--space-4);
		padding-top: var(--space-3);
	}

	.kicker {
		margin: 0;
		font: 500 var(--font-size-xs) / 1 var(--font-mono);
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--accent);
	}

	h1 {
		margin: var(--space-1) 0 0;
		font: 500 var(--font-size-2xl) / 1.1 var(--font-display);
	}

	.seasons {
		display: flex;
		gap: var(--space-3);
		margin-top: var(--space-2);
		font: 500 var(--font-size-sm) / 1 var(--font-mono);
	}

	.seasons a {
		color: var(--muted);
		text-decoration: none;
		padding-bottom: 2px;
		border-bottom: 1px solid transparent;
	}

	.seasons a[aria-current='page'] {
		color: var(--ink);
		border-bottom-color: var(--ink);
	}

	.after {
		margin: var(--space-2) 0 0;
		font-size: var(--font-size-md);
		color: var(--ink);
	}

	.after .count {
		color: var(--muted);
		font-size: var(--font-size-sm);
	}

	.notice {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--accent);
		border-radius: var(--radius-lg);
		background: var(--accent-wash);
		color: var(--ink);
	}

	.notice p {
		margin: 0;
		font-size: var(--font-size-sm);
	}

	.count-all {
		min-height: 40px;
		padding: 0 var(--space-4);
		border: 0;
		border-radius: var(--radius-md);
		background: var(--accent);
		color: var(--accent-ink);
		font: inherit;
		font-weight: 600;
		cursor: pointer;
	}

	.change {
		display: inline-flex;
		font-size: var(--font-size-xs);
		color: var(--muted);
		align-items: center;
		gap: var(--space-2);
	}

	.change select {
		max-width: 15rem;
		min-height: 32px;
		border: 1px solid var(--rule-strong);
		border-radius: var(--radius-sm);
		background: var(--panel);
		color: var(--ink);
		font: inherit;
		font-size: var(--font-size-xs);
		padding: 0 var(--space-2);
	}

	.layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: var(--space-4);
		align-items: start;
	}

	@media (min-width: 1000px) {
		.layout {
			grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
		}
	}

	.side {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.card {
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		background: var(--panel);
		box-shadow: var(--elev-1);
		padding: var(--space-4);
	}

	h2 {
		margin: 0 0 var(--space-2);
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}

	ol {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.drivers li {
		display: grid;
		grid-template-columns: 1.6rem minmax(0, 1fr) 4.5rem 64px;
		gap: var(--space-2) var(--space-3);
		align-items: center;
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}

	.drivers .wins,
	.drivers .gap {
		display: none;
	}

	@media (min-width: 640px) {
		.drivers li {
			grid-template-columns: 1.6rem minmax(0, 1fr) 4rem 100px 4rem 3.5rem;
		}
		.drivers .wins,
		.drivers .gap {
			display: block;
		}
	}

	.pos {
		font: 500 var(--font-size-sm) / 1 var(--font-mono);
		color: var(--muted);
	}

	.who {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
		min-width: 0;
	}

	.bar {
		flex: none;
		width: 4px;
		height: 16px;
		border-radius: 2px;
		align-self: center;
	}

	.name {
		font-family: var(--font-display);
		font-size: var(--font-size-md);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.team {
		font-size: var(--font-size-xs);
		color: var(--muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		display: none;
	}

	@media (min-width: 640px) {
		.team {
			display: inline;
		}
	}

	.wins {
		font-size: var(--font-size-xs);
		color: var(--muted);
	}

	.spark {
		display: flex;
		align-items: center;
	}

	.drivers .spark :global(svg) {
		width: 100%;
		height: 22px;
	}

	.gap {
		font: 500 var(--font-size-xs) / 1 var(--font-mono);
		color: var(--ink-2);
		text-align: right;
	}

	.points {
		font: 600 var(--font-size-md) / 1 var(--font-mono);
		text-align: right;
	}

	.drivers .points {
		grid-column: -2;
	}

	.foot {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
		margin: var(--space-3) 0 0;
		font-size: var(--font-size-xs);
		color: var(--muted);
	}

	.link {
		border: 0;
		padding: 0;
		background: none;
		color: var(--accent);
		font: inherit;
		cursor: pointer;
	}

	.chart {
		display: block;
		width: 100%;
		height: auto;
	}

	.chart line {
		stroke: var(--rule);
	}

	.chart polyline {
		fill: none;
		stroke-width: 2.5;
		stroke-linejoin: round;
		stroke-linecap: round;
	}

	.chart text {
		font: 500 10px var(--font-mono);
	}

	.chart .axis {
		fill: var(--muted);
	}

	.chart .end {
		font-size: 11px;
	}

	.note {
		margin: var(--space-2) 0 0;
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}

	.teams li {
		display: grid;
		grid-template-columns: 1.6rem minmax(0, 1fr) 4rem;
		gap: var(--space-3);
		align-items: center;
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}

	.muted {
		color: var(--muted);
	}
</style>
