<!--
  Timing: the design's tower, in two views. "Timing" is position, places
  gained since the start, driver, interval (or gap to the leader), last lap
  (purple for the session's fastest, green for a personal best), tyre, the
  stints so far and stops. "Sectors" is the last lap's three sectors, coloured
  the same way, and each driver's best lap. Tapping a driver focuses the
  undercut watch on their battle. Everything comes from Delay Sync's released
  rows; nothing here fetches, and the stint bars stop at the viewer's lap.
-->
<script lang="ts">
	import { stintsForBar, stopsMade, type DriverRow } from '$lib/timing-tower';
	import { colorForTeam } from '$lib/teams';
	import { flip } from 'svelte/animate';
	import { PositionChange, StintBar, Tabs, TyreBadge } from '$lib/ui';
	import RollNumber from '$lib/ui/RollNumber.svelte';
	import type { FocusPair } from './focus';
	import { fmtLap, surname } from './format';

	type LapClass = '' | 'fastest' | 'pb';

	interface Props {
		rows: DriverRow[];
		focus: string | null;
		pair: FocusPair | null;
		names: Record<string, string>;
		/** The lap the viewer has reached, which the stint bars stop at. */
		currentLap: number;
		totalLaps: number | null;
		lastLapClass: (driver: string, lapTime: number | null) => LapClass;
		sectorClass: (driver: string, index: number, time: number | null) => LapClass;
		onFocus: (driver: string) => void;
	}

	let {
		rows,
		focus,
		pair,
		names,
		currentLap,
		totalLaps,
		lastLapClass,
		sectorClass,
		onFocus
	}: Props = $props();

	let expanded = $state(false);

	/** An overtake slides the rows into their new order (W2 D4); with
	 * reduced motion they simply swap. */
	const reduceMotion =
		typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const FLIP_MS = reduceMotion ? 0 : 240;
	let view = $state<'timing' | 'sectors'>('timing');
	let gapMode = $state<'interval' | 'leader'>('interval');

	const VIEWS = [
		{ id: 'timing', label: 'Timing' },
		{ id: 'sectors', label: 'Sectors' }
	];

	/** Rows held in place until the driver list arrives (behind the viewer's
	 * delay). An empty tower that then filled with twenty rows pushed every
	 * panel below and beside it down at once: most of the page's layout
	 * shift (CLS 0.41 on a desktop replay, measured by `just verify-real`). */
	const PLACEHOLDER_ROWS = 20;

	function interval(row: DriverRow): string {
		if (row.lapsDown !== null && row.position !== 1) return `+${row.lapsDown}L`;
		if (row.intervalToAheadS === null) return '—';
		return `+${row.intervalToAheadS.toFixed(3)}`;
	}

	function gap(row: DriverRow): string {
		if (row.position === 1) return '';
		if (row.lapsDown !== null) return `+${row.lapsDown}L`;
		return row.gapToLeaderS === null ? '—' : `+${row.gapToLeaderS.toFixed(3)}`;
	}

	const sector = (time: number | null | undefined) =>
		time === null || time === undefined ? '—' : time.toFixed(3);

	const LAP_TITLE: Record<LapClass, string | undefined> = {
		fastest: 'Fastest so far',
		pb: 'Personal best',
		'': undefined
	};

	/** `index` is the row's place in the already-sorted list. The phone's
	 * top-ten view is decided by it, never by `position`: before positions
	 * arrive, or while two cars briefly share one, a position test showed
	 * zero or eleven rows and the panels below bounced. */
	function rowClass(row: DriverRow, index: number): string {
		const classes: string[] = [];
		if (row.driver === (focus ?? pair?.a)) classes.push('is-focus');
		else if (pair && (row.driver === pair.a || row.driver === pair.b)) classes.push('is-rival');
		if (row.inPit) classes.push('pit');
		if (row.retired) classes.push('out');
		if (index >= 10) classes.push('extra');
		return classes.join(' ');
	}
</script>

<section class="panel p-tower" aria-labelledby="towerTitle" data-testid="tower-panel">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="towerTitle">Timing</h2>
			<p class="panel-sub">Tap a driver to focus the undercut watch</p>
		</div>
		{#if view === 'timing'}
			<div class="seg" role="group" aria-label="Time column">
				<button
					type="button"
					aria-pressed={gapMode === 'interval'}
					onclick={() => (gapMode = 'interval')}
					title="Interval to the car ahead">Int</button
				>
				<button
					type="button"
					aria-pressed={gapMode === 'leader'}
					onclick={() => (gapMode = 'leader')}
					title="Gap to the leader">Gap</button
				>
			</div>
		{/if}
	</div>
	<div class="tower-tabs">
		<Tabs
			tabs={VIEWS}
			selected={view}
			idPrefix="tower"
			label="Timing views"
			onSelect={(id) => (view = id === 'sectors' ? 'sectors' : 'timing')}
		/>
	</div>
	{#if rows.length === 0}
		<p class="visually-hidden" role="status">Waiting for the driver list…</p>
		<div class="tower-scroll" aria-hidden="true">
			<table class="tower tower-waiting">
				<thead>
					<tr>
						<th scope="col">Pos</th>
						<th scope="col" class="col-gain"><span class="visually-hidden">Places</span></th>
						<th scope="col" class="l">Driver</th>
						<th scope="col">{gapMode === 'interval' ? 'Int' : 'Gap'}</th>
						<th scope="col">Last lap</th>
						<th scope="col">Tyre</th>
						<th scope="col" class="col-stints">Stints</th>
					</tr>
				</thead>
				<tbody class="tower-body">
					{#each Array.from({ length: PLACEHOLDER_ROWS }, (_, i) => i + 1) as position (position)}
						<tr class={position > 10 ? 'extra' : ''} data-testid="tower-placeholder">
							<td class="pos">{position}</td>
							<td class="col-gain"></td>
							<td class="l"><span class="drvbtn"><span class="code">—</span></span></td>
							<td class="num">—</td>
							<td class="num t-last">—</td>
							<td><span class="age num">—</span></td>
							<td class="col-stints"></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<button class="btn tower-more" type="button" disabled aria-hidden="true"
			>Show all {PLACEHOLDER_ROWS}</button
		>
		<div class="tower-legend" aria-hidden="true">
			<span><i style="background:var(--fastest)"></i>Fastest</span>
			<span><i style="background:var(--pb)"></i>Personal best</span>
			<span><i style="background:var(--accent)"></i>Focused battle</span>
		</div>
	{:else}
		<div
			class="tower-scroll"
			id="tower-panel-{view}"
			role="tabpanel"
			aria-labelledby="tower-tab-{view}"
		>
			<table class="tower" data-testid="timing-tower">
				<thead>
					{#if view === 'timing'}
						<tr>
							<th scope="col">Pos</th>
							<th scope="col" class="col-gain"
								><span class="visually-hidden">Places since the start</span></th
							>
							<th scope="col" class="l">Driver</th>
							<th scope="col">{gapMode === 'interval' ? 'Int' : 'Gap'}</th>
							<th scope="col">Last lap</th>
							<th scope="col">Tyre</th>
							<th scope="col" class="col-stints">Stints</th>
							<th scope="col" class="col-stops">Stops</th>
						</tr>
					{:else}
						<tr>
							<th scope="col">Pos</th>
							<th scope="col" class="l">Driver</th>
							<th scope="col">S1</th>
							<th scope="col">S2</th>
							<th scope="col">S3</th>
							<th scope="col">Best lap</th>
						</tr>
					{/if}
				</thead>
				<tbody class="tower-body" class:expanded>
					{#each rows as row, index (row.driver)}
						{@const name = names[row.driver] ?? row.name ?? row.driver}
						<tr
							animate:flip={{ duration: FLIP_MS }}
							class={rowClass(row, index)}
							data-testid="tower-row"
							data-driver={row.driver}
							data-code={row.code}
							data-lap={row.lapNumber}
							data-pos={row.position}
							data-pit={row.inPit}
							data-down={row.lapsDown}
							data-last={row.lastLapTimeS}
							data-best={row.bestLapS}
							data-compound={row.compound?.toLowerCase()}
							data-stops={stopsMade(row)}
							data-retired={row.retired}
							onclick={() => onFocus(row.driver)}
						>
							<td class="pos"
								>{#if row.retired}<span class="outchip" title="Out of the race">OUT</span
									>{:else}{row.position ?? '—'}{/if}</td
							>
							{#if view === 'timing'}
								<td class="col-gain"
									>{#if !row.retired}<PositionChange
											start={row.startPosition}
											now={row.position}
										/>{/if}</td
								>
							{/if}
							<td class="l">
								<button
									type="button"
									class="drvbtn"
									aria-pressed={row.driver === focus}
									aria-label={`Focus ${name}`}
									title={name}
								>
									<span class="sw" style:background={colorForTeam(row.team)}></span>
									<span class="code">{row.code ?? row.driver}</span>
									<span class="fname">{surname(name) ?? ''}</span>
								</button>
							</td>
							{#if view === 'timing'}
								{@const lastClass = lastLapClass(row.driver, row.lastLapTimeS)}
								<td class="num">
									{#if row.inPit}<span class="pitchip">PIT</span>{:else if row.position === 1}<span
											class="caps"
											style="font-size:10.5px">Leader</span
										>{:else}<RollNumber
											value={gapMode === 'interval' ? interval(row) : gap(row)}
										/>{/if}
								</td>
								<td class="num t-last {lastClass}" title={LAP_TITLE[lastClass]}
									>{#key row.lastLapTimeS}<span
											class="lap-val"
											class:flash={lastClass === 'fastest'}>{fmtLap(row.lastLapTimeS)}</span
										>{/key}</td
								>
								<td><TyreBadge compound={row.compound} age={row.tyreAge} /></td>
								<td class="col-stints"
									><StintBar stints={stintsForBar(row)} {currentLap} {totalLaps} /></td
								>
								<td class="num col-stops">{stopsMade(row) ?? '—'}</td>
							{:else}
								{#each [0, 1, 2] as i (i)}
									{@const time = row.lastSectorsS?.[i] ?? null}
									{@const cls = sectorClass(row.driver, i, time)}
									<td class="num t-sector {cls}" title={LAP_TITLE[cls]}>{sector(time)}</td>
								{/each}
								{@const bestClass = lastLapClass(row.driver, row.bestLapS)}
								<td
									class="num t-last {bestClass === 'fastest' ? 'fastest' : ''}"
									title={bestClass === 'fastest' ? 'Fastest so far' : undefined}
									>{fmtLap(row.bestLapS)}</td
								>
							{/if}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if rows.length > 10}
			<button
				class="btn tower-more"
				type="button"
				aria-expanded={expanded}
				onclick={() => (expanded = !expanded)}
				>{expanded ? 'Show top 10' : `Show all ${rows.length}`}</button
			>
		{/if}
		<div class="tower-legend">
			<span><i style="background:var(--fastest)"></i>Fastest</span>
			<span><i style="background:var(--pb)"></i>Personal best</span>
			<span><i style="background:var(--accent)"></i>Focused battle</span>
		</div>
	{/if}
</section>
