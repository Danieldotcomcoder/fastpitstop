<!--
  The design system v2 gallery (ROADMAP W2 D2): every component, in either
  theme, with real values from the 2024 Miami Grand Prix at lap 33. A dev
  page like /track-map: kept out of search (seo.ts NOINDEX_PREFIXES), and
  the place to check a component before a screen uses it.
-->
<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api/client';
	import { outlineFromApi } from '$lib/track-map';
	import type { Point } from '$lib/track-map/types';
	import type { TrackStatusTone } from '$lib/desk/race-feed';
	import {
		BottomNav,
		Countdown,
		DeltaChip,
		FlagBanner,
		GapBar,
		PositionChange,
		Sheet,
		Skeleton,
		Sparkline,
		StatTile,
		StintBar,
		Tabs,
		Toaster,
		ToastQueue,
		TrackThumbnail,
		TyreBadge
	} from '$lib/ui';

	type Theme = 'dark' | 'light';
	let theme = $state<Theme>('dark');
	onMount(() => {
		theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
	});
	function setTheme(next: Theme): void {
		theme = next;
		const root = document.documentElement;
		if (next === 'light') root.dataset.theme = 'light';
		else delete root.dataset.theme;
	}

	// Miami 2024, end of lap 33 (the mockups' data).
	const ROWS = [
		{
			pos: 1,
			code: 'NOR',
			start: 5,
			gap: 0,
			tyre: 'HARD',
			age: 4,
			stints: [
				['MEDIUM', 1, 29],
				['HARD', 30, null]
			]
		},
		{
			pos: 2,
			code: 'VER',
			start: 1,
			gap: 0.917,
			tyre: 'HARD',
			age: 10,
			stints: [
				['MEDIUM', 1, 23],
				['HARD', 24, null]
			]
		},
		{
			pos: 3,
			code: 'LEC',
			start: 2,
			gap: 1.269,
			tyre: 'HARD',
			age: 14,
			stints: [
				['MEDIUM', 1, 19],
				['HARD', 20, null]
			]
		},
		{
			pos: 4,
			code: 'PIA',
			start: 7,
			gap: 1.874,
			tyre: 'HARD',
			age: 6,
			stints: [
				['MEDIUM', 1, 27],
				['HARD', 28, null]
			]
		},
		{
			pos: 5,
			code: 'SAI',
			start: 3,
			gap: 2.385,
			tyre: 'HARD',
			age: 6,
			stints: [
				['MEDIUM', 1, 27],
				['HARD', 28, null]
			]
		},
		{
			pos: 6,
			code: 'PER',
			start: 4,
			gap: 2.899,
			tyre: 'MEDIUM',
			age: 10,
			stints: [
				['MEDIUM', 1, 17],
				['HARD', 18, 28],
				['MEDIUM', 29, null]
			]
		}
	] as const;
	const stintsOf = (row: (typeof ROWS)[number]) =>
		row.stints.map(([compound, startLap, endLap]) => ({ compound, startLap, endLap }));
	const BATTLE = [2.71, 2.38, 3.79, 0.61, 2.4, 2.19, 0.49, 0.44];
	const TONES: TrackStatusTone[] = ['green', 'yellow', 'sc', 'vsc', 'red', 'chequered'];

	let delta = $state(-0.05);
	let tab = $state('tower');
	let nav = $state('race');
	let sheetOpen = $state(false);
	const toasts = new ToastQueue();
	let toastRevision = $state(0);
	let outline = $state<Point[] | null>(null);
	let outlineName = $state('');
	let startUtc = $state(new Date(Date.now() + (2 * 24 + 4) * 3_600_000).toISOString());

	onMount(async () => {
		startUtc = new Date(Date.now() + (2 * 24 + 4) * 3_600_000 + 12 * 60_000).toISOString();
		try {
			const { data: sessions } = await api.GET('/sessions');
			const first = sessions?.[0];
			if (!first) return;
			const { data } = await api.GET('/sessions/{key}/circuit', {
				params: { path: { key: first.key } }
			});
			const parsed = outlineFromApi(data?.outline);
			if (parsed) {
				outline = parsed.points;
				outlineName = first.event_name ?? first.key;
			}
		} catch {
			// No API: the thumbnail says so instead.
		}
	});

	const NAV = [
		{ id: 'race', label: 'Race', icon: 'M4 6h16M4 12h16M4 18h10' },
		{ id: 'battle', label: 'Battle', icon: 'M6 18L18 6M8 6h10v10' },
		{ id: 'map', label: 'Map', icon: 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z' },
		{ id: 'strategy', label: 'Strategy', icon: 'M4 18V9M10 18V5M16 18v-6M22 18H2' },
		{ id: 'story', label: 'Story', icon: 'M5 4h14v16l-7-4-7 4z' }
	];
</script>

<div class="gallery">
	<header class="top">
		<div>
			<h1>Design system v2</h1>
			<p>Every component, with real values from the 2024 Miami Grand Prix at lap 33.</p>
		</div>
		<div class="theme" role="group" aria-label="Theme">
			{#each ['dark', 'light'] as const as option (option)}
				<button type="button" aria-pressed={theme === option} onclick={() => setTheme(option)}
					>{option}</button
				>
			{/each}
		</div>
	</header>

	<section>
		<h2>Stat tiles</h2>
		<div class="row">
			<StatTile label="Gap for P4" value="0.44" unit="s" size="hero">
				{#snippet sub()}Sainz behind Piastri, down from 2.71 s on lap 26{/snippet}
			</StatTile>
			<StatTile label="Lap" value="33" unit="/ 57" />
			<StatTile label="Undercut crossover" value="35" size="lg" confidence="low">
				{#snippet sub()}Leclerc on Verstappen{/snippet}
			</StatTile>
			<StatTile label="Track" value={null} size="lg" />
		</div>
	</section>

	<section>
		<h2>Tyres, stints, places, gaps</h2>
		<table class="mini-tower">
			<tbody>
				{#each ROWS as row (row.code)}
					<tr>
						<td class="num">{row.pos}</td>
						<td>{row.code}</td>
						<td><PositionChange start={row.start} now={row.pos} /></td>
						<td class="num">{row.gap === 0 ? 'Leader' : `+${row.gap.toFixed(3)}`}</td>
						<td class="gapcell"><GapBar gap={row.gap} max={3} /></td>
						<td><TyreBadge compound={row.tyre} age={row.age} /></td>
						<td class="stints"
							><StintBar stints={stintsOf(row)} currentLap={33} totalLaps={57} /></td
						>
					</tr>
				{/each}
			</tbody>
		</table>
		<p class="note">
			Stint bars stop at lap 33. Unknown race distance:
			<span class="inline-bar"
				><StintBar stints={stintsOf(ROWS[5])} currentLap={33} totalLaps={null} /></span
			>
		</p>
		<div class="row">
			{#each ['SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET', null] as compound (compound)}
				<TyreBadge {compound} age={compound ? 12 : null} size={22} />
			{/each}
		</div>
	</section>

	<section>
		<h2>Trends and deltas</h2>
		<div class="row">
			<Sparkline
				values={BATTLE}
				bands={[[2, 6]]}
				width={260}
				height={64}
				label="Gap from Sainz to Piastri, laps 26 to 33"
			/>
			<Sparkline
				values={[1.2, 1.1, null, 0.9, 0.7, 0.4]}
				width={160}
				height={40}
				label="With a gap in the data"
			/>
			<div class="deltas">
				<DeltaChip value={delta} digits={2} unit=" s" good="negative" />
				<DeltaChip value={-delta} digits={2} unit=" s" good="negative" />
				<DeltaChip value={0} digits={2} unit=" s" good="negative" />
				<DeltaChip value={null} />
				<button
					type="button"
					onclick={() => (delta = Math.round((Math.random() - 0.5) * 300) / 100)}>Roll</button
				>
			</div>
		</div>
	</section>

	<section>
		<h2>Flag banners</h2>
		<div class="stack">
			{#each TONES as tone (tone)}
				<div class="tone-row">
					<code>{tone}</code>
					<FlagBanner {tone} detail={tone === 'sc' ? 'since lap 28' : null} />
					{#if tone === 'green'}<span class="note">Nothing on green.</span>{/if}
				</div>
			{/each}
		</div>
	</section>

	<section>
		<h2>Countdown and track</h2>
		<div class="row">
			<Countdown {startUtc} />
			<div class="thumb">
				{#if outline}
					<TrackThumbnail points={outline} label={outlineName} draw width={220} height={150} />
					<span class="note">{outlineName}</span>
				{:else}
					<Skeleton width="220px" height="150px" label="No outline: the API isn't reachable" />
				{/if}
			</div>
		</div>
	</section>

	<section>
		<h2>Tabs, sheet, toasts, skeleton</h2>
		<Tabs
			tabs={[
				{ id: 'tower', label: 'Tower' },
				{ id: 'strategy', label: 'Strategy' },
				{ id: 'story', label: 'Story' }
			]}
			selected={tab}
			idPrefix="demo"
			label="Demo views"
			onSelect={(id) => (tab = id)}
		/>
		<div class="panel" id="demo-panel-{tab}" role="tabpanel" aria-labelledby="demo-tab-{tab}">
			Showing the {tab} panel.
		</div>
		<div class="row">
			<button type="button" onclick={() => (sheetOpen = true)}>Open a sheet</button>
			<button
				type="button"
				onclick={() => {
					toasts.push(
						'Safety car from lap 28, after Sargeant retired.',
						Date.now(),
						'Lap 28 · Safety car'
					);
					toastRevision++;
				}}>Show a toast</button
			>
		</div>
		<div class="skeletons">
			<Skeleton lines={3} /><Skeleton height="64px" radius="var(--radius-md)" />
		</div>
	</section>

	<section>
		<h2>Bottom navigation (phone)</h2>
		<div class="phone">
			<div class="phone-body">Race view</div>
			<BottomNav items={NAV} selected={nav} label="Race views" onSelect={(id) => (nav = id)} />
		</div>
	</section>
</div>

<Sheet open={sheetOpen} title="Delay" onClose={() => (sheetOpen = false)}>
	<p>The sheet holds settings and details on a phone, a thumb's reach from the bottom.</p>
</Sheet>
<Toaster queue={toasts} revision={toastRevision} />

<style>
	.gallery {
		max-width: 1100px;
		margin: 0 auto;
		padding: var(--space-5) var(--space-4) var(--space-8);
		display: flex;
		flex-direction: column;
		gap: var(--space-6);
	}
	.top {
		display: flex;
		justify-content: space-between;
		align-items: flex-end;
		gap: var(--space-4);
		flex-wrap: wrap;
	}
	h1 {
		margin: 0;
		font: 500 var(--font-size-2xl) / 1.1 var(--font-display);
	}
	h2 {
		margin: 0 0 var(--space-3);
		font: 500 var(--font-size-lg) / 1.2 var(--font-display);
	}
	p {
		margin: var(--space-1) 0 0;
		color: var(--ink-2);
	}
	section {
		padding: var(--space-4);
		background: var(--panel);
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		box-shadow: var(--elev-1);
	}
	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-5);
		align-items: flex-end;
	}
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}
	.tone-row {
		display: grid;
		grid-template-columns: 90px 1fr;
		align-items: center;
		gap: var(--space-3);
	}
	.mini-tower {
		width: 100%;
		border-collapse: collapse;
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
	}
	.mini-tower td {
		padding: 6px 8px 6px 0;
		border-top: 1px solid var(--rule);
	}
	.gapcell {
		width: 80px;
	}
	.stints {
		width: 30%;
	}
	.inline-bar {
		display: inline-block;
		width: 160px;
		vertical-align: middle;
	}
	.note {
		font-size: var(--font-size-sm);
		color: var(--muted);
	}
	.deltas {
		display: flex;
		gap: var(--space-2);
		align-items: center;
	}
	.thumb {
		width: 220px;
	}
	.panel {
		padding: var(--space-4) 0;
		color: var(--ink-2);
	}
	.skeletons {
		margin-top: var(--space-4);
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--space-4);
	}
	.phone {
		width: 390px;
		max-width: 100%;
		height: 260px;
		display: flex;
		flex-direction: column;
		border: 1px solid var(--rule);
		border-radius: var(--radius-lg);
		overflow: hidden;
		background: var(--paper);
	}
	.phone-body {
		flex: 1;
		display: grid;
		place-items: center;
		color: var(--muted);
	}
	button {
		min-height: 36px;
		padding: 0 var(--space-3);
		border: 1px solid var(--rule-strong);
		border-radius: var(--radius-md);
		background: var(--panel);
		color: var(--ink);
		font: inherit;
		cursor: pointer;
	}
	.theme button {
		text-transform: capitalize;
	}
	button[aria-pressed='true'] {
		background: var(--ink);
		color: var(--paper);
	}
	.theme {
		display: flex;
		gap: var(--space-1);
	}
	code {
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
		color: var(--muted);
	}
</style>
