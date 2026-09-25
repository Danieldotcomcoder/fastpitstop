<!--
  Timing: the design's tower. Position, driver, interval, gap, last lap
  (purple for the session's fastest, green for a personal best), tyre and
  stops. Tapping a driver focuses the undercut watch on their battle.
  Everything comes from Delay Sync's released rows; nothing here fetches.
-->
<script lang="ts">
	import type { DriverRow } from '$lib/timing-tower';
	import { colorForTeam } from '$lib/teams';
	import type { FocusPair } from './focus';
	import { compoundLetter, compoundName, fmtLap, surname } from './format';

	interface Props {
		rows: DriverRow[];
		focus: string | null;
		pair: FocusPair | null;
		names: Record<string, string>;
		lastLapClass: (driver: string, lapTime: number | null) => '' | 'fastest' | 'pb';
		onFocus: (driver: string) => void;
	}

	let { rows, focus, pair, names, lastLapClass, onFocus }: Props = $props();

	let expanded = $state(false);

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

	function rowClass(row: DriverRow): string {
		const classes: string[] = [];
		if (row.driver === (focus ?? pair?.a)) classes.push('is-focus');
		else if (pair && (row.driver === pair.a || row.driver === pair.b)) classes.push('is-rival');
		if (row.inPit) classes.push('pit');
		if ((row.position ?? 99) > 10) classes.push('extra');
		return classes.join(' ');
	}
</script>

<section class="panel p-tower" aria-labelledby="towerTitle" data-testid="tower-panel">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="towerTitle">Timing</h2>
			<p class="panel-sub">Tap a driver to focus the undercut watch</p>
		</div>
	</div>
	{#if rows.length === 0}
		<div class="empty">Waiting for the driver list…</div>
	{:else}
		<div class="tower-scroll">
			<table class="tower" data-testid="timing-tower">
				<thead>
					<tr>
						<th scope="col">Pos</th>
						<th scope="col" class="l">Driver</th>
						<th scope="col">Int</th>
						<th scope="col" class="col-gap">Gap</th>
						<th scope="col">Last lap</th>
						<th scope="col">Tyre</th>
						<th scope="col" class="col-stops">Stops</th>
					</tr>
				</thead>
				<tbody class="tower-body" class:expanded>
					{#each rows as row (row.driver)}
						{@const name = names[row.driver] ?? row.name ?? row.driver}
						{@const lastClass = lastLapClass(row.driver, row.lastLapTimeS)}
						{@const letter = compoundLetter(row.compound)}
						<tr
							class={rowClass(row)}
							data-testid="tower-row"
							data-driver={row.driver}
							onclick={() => onFocus(row.driver)}
						>
							<td class="pos">{row.position ?? '—'}</td>
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
							<td class="num">
								{#if row.inPit}<span class="pitchip">PIT</span>{:else if row.position === 1}<span
										class="caps"
										style="font-size:10.5px">Leader</span
									>{:else}{interval(row)}{/if}
							</td>
							<td class="num col-gap">{gap(row)}</td>
							<td
								class="num t-last {lastClass}"
								title={lastClass === 'fastest'
									? 'Fastest lap so far'
									: lastClass === 'pb'
										? 'Personal best'
										: undefined}>{fmtLap(row.lastLapTimeS)}</td
							>
							<td>
								<span class="tyre">
									<span class="tyre-disc {letter}" title={compoundName(row.compound)}>{letter}</span
									><span class="age num">{row.tyreAge ?? '—'}</span>
								</span>
							</td>
							<td class="num col-stops">{row.stintNumber !== null ? row.stintNumber - 1 : '—'}</td>
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
			<span><i style="background:var(--fastest)"></i>Fastest lap</span>
			<span><i style="background:var(--pb)"></i>Personal best</span>
			<span><i style="background:var(--accent)"></i>Focused battle</span>
		</div>
	{/if}
</section>
