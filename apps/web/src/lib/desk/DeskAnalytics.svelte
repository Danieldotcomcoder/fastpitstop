<!--
  The analytics panel: three views of the focused battle behind accessible
  tabs (arrow keys move between them), the chosen one remembered per viewer.
  Only the active view mounts, so the braking analysis is requested only
  when someone opens it.
-->
<script lang="ts">
	import type { components } from '$lib/api/client';
	import BrakeChart from './BrakeChart.svelte';
	import DegChart from './DegChart.svelte';
	import type { FocusPair } from './focus';
	import GapChart from './GapChart.svelte';
	import type { Crossover } from './pit-board';
	import { hideTip } from './tooltip';

	type Lap = components['schemas']['LapRowOut'];

	interface Props {
		apiKey: string;
		laps: Lap[];
		pair: FocusPair | null;
		crossover: Crossover | null;
		codes: Record<string, string>;
		names: Record<string, string>;
		teams: Record<string, string | null>;
		totalLaps: number | null;
		isStintRevealed: (driver: string, stint: number) => boolean;
		revision: number;
	}

	let props: Props = $props();

	const TABS = [
		{ id: 'deg', label: 'Degradation' },
		{ id: 'gap', label: 'Gap to leader' },
		{ id: 'brake', label: 'Braking drift' }
	] as const;
	type TabId = (typeof TABS)[number]['id'];

	function stored(): TabId {
		try {
			const value = localStorage.getItem('pitwall.tab');
			if (value === 'deg' || value === 'gap' || value === 'brake') return value;
		} catch {
			// storage unavailable (private window, blocked site data)
		}
		return 'deg';
	}

	let tab = $state<TabId>(stored());
	let buttons: HTMLButtonElement[] = [];

	function select(id: TabId): void {
		tab = id;
		hideTip();
		try {
			localStorage.setItem('pitwall.tab', id);
		} catch {
			// per-viewer convenience only
		}
	}

	function onKey(event: KeyboardEvent): void {
		const index = TABS.findIndex((t) => t.id === tab);
		let next: number | null = null;
		if (event.key === 'ArrowRight') next = (index + 1) % TABS.length;
		else if (event.key === 'ArrowLeft') next = (index - 1 + TABS.length) % TABS.length;
		else if (event.key === 'Home') next = 0;
		else if (event.key === 'End') next = TABS.length - 1;
		if (next === null) return;
		event.preventDefault();
		select(TABS[next]!.id);
		buttons[next]?.focus();
	}
</script>

<section class="panel p-analytics" aria-labelledby="anTitle" data-testid="analytics-panel">
	<h2 class="sr-only" id="anTitle">Analytics</h2>
	<div class="tabs" role="tablist" aria-label="Analytics views" tabindex="-1" onkeydown={onKey}>
		{#each TABS as t, index (t.id)}
			<button
				bind:this={buttons[index]}
				role="tab"
				id="tab-{t.id}"
				aria-controls="view"
				aria-selected={tab === t.id}
				tabindex={tab === t.id ? 0 : -1}
				onclick={() => select(t.id)}>{t.label}</button
			>
		{/each}
	</div>
	<div id="view" role="tabpanel" aria-labelledby="tab-{tab}">
		{#if tab === 'deg'}
			<DegChart
				laps={props.laps}
				pair={props.pair}
				crossover={props.crossover}
				codes={props.codes}
				teams={props.teams}
			/>
		{:else if tab === 'gap'}
			<GapChart
				laps={props.laps}
				pair={props.pair}
				codes={props.codes}
				teams={props.teams}
				totalLaps={props.totalLaps}
			/>
		{:else}
			<BrakeChart
				apiKey={props.apiKey}
				pair={props.pair}
				codes={props.codes}
				names={props.names}
				teams={props.teams}
				isStintRevealed={props.isStintRevealed}
				revision={props.revision}
			/>
		{/if}
	</div>
</section>
