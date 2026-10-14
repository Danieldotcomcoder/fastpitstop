<!--
  The phone's Race tab (ROADMAP W7, mockup A): the race in three seconds.
  The focused battle, what could happen next, the top five and the latest
  explained moment, all read from the same released state as the rest of
  the desk, so the glance never knows more than the full desk does. Tapping
  a car makes it the battle; "All" opens the full tower.
-->
<script lang="ts">
	import Sparkline from '$lib/ui/Sparkline.svelte';
	import TyreBadge from '$lib/ui/TyreBadge.svelte';
	import { colorForTeam } from '$lib/teams';
	import type { DriverRow } from '$lib/timing-tower';
	import { anomalyKindLabel, type ExplanationView } from '$lib/why/view';
	import ConfBadge from './ConfBadge.svelte';
	import { compoundLetter, surname } from './format';
	import { battleVerdict, type BattleTrend } from './hero';
	import type { BoardDriver, BoardModel } from './pit-board';

	interface Props {
		rows: readonly DriverRow[];
		model: BoardModel;
		/** The pair's gap lap by lap, the same trend the desktop's battle
		 * card draws (`battleTrend`). */
		trend: BattleTrend | null;
		names: Record<string, string>;
		explanations: readonly ExplanationView[];
		onFocus: (driver: string) => void;
		onAll: () => void;
	}

	let { rows, model, trend, names, explanations, onFocus, onAll }: Props = $props();

	const TOP = 5;

	let a = $derived(model.attacker);
	let b = $derived(model.defender);
	let trendValues = $derived(trend?.points.map((p) => p.gapS) ?? []);
	/** From zero, as the desktop's battle card draws it: a gap holding at
	 * 3 s must not look like it swings because the axis zoomed in. */
	let trendDomain = $derived([0, Math.max(0.5, ...trendValues) * 1.08] as const);
	/** Neutralised laps, shaded under the line (index ranges). */
	let trendBands = $derived(
		(trend?.points ?? []).flatMap((p, i) =>
			i > 0 && p.neutral !== null ? [[i - 1, i] as const] : []
		)
	);
	let top = $derived(rows.filter((r) => r.position !== null).slice(0, TOP));
	let latest = $derived([...explanations].reverse().find((e) => e.grounded) ?? null);

	const who = (d: BoardDriver) => surname(names[d.driver]) ?? d.code;
	let line = $derived(a && b ? battleVerdict(trend, a, b, who(a), who(b)) : '');
	const tyre = (d: BoardDriver) =>
		[d.position !== null ? `P${d.position}` : null, d.compound ? compoundLetter(d.compound) : null]
			.filter(Boolean)
			.join(' · ') + (d.tyreAge !== null ? ` ${d.tyreAge}` : '');

	const whyLabel = (e: ExplanationView) =>
		[e.lapNumber !== null ? `L${e.lapNumber}` : null, anomalyKindLabel(e.kind)]
			.filter(Boolean)
			.join(' · ');

	function gap(row: DriverRow): string {
		if (row.position === 1) return 'Leader';
		if (row.lapsDown !== null) return `+${row.lapsDown}L`;
		return row.gapToLeaderS === null ? '—' : `+${row.gapToLeaderS.toFixed(3)}`;
	}
</script>

<!-- Every block keeps its size from the first paint, filled or not: the
     glance fills in as the race is released, and growing blocks pushed
     everything under them down (layout shift 0.25 at 390px). -->
<section class="panel p-glance" aria-label="The race at a glance" data-testid="desk-glance">
	<div class="glance-battle">
		<div class="caps">
			The battle{#if b?.position}&nbsp;· for P{b.position}{/if}
		</div>
		<div class="duel">
			<div class="side">
				<span class="name"
					>{#if a}<i style:background={colorForTeam(a.team)}></i>{who(a)}{:else}—{/if}</span
				>
				<span class="meta num">{a ? tyre(a) : ''}&nbsp;</span>
			</div>
			<div class="gap num" aria-label="Gap between them">
				{a && b && model.intervalS !== null ? model.intervalS.toFixed(2) : '—'}<small>s</small>
			</div>
			<div class="side right">
				<span class="name"
					>{#if b}{who(b)}<i style:background={colorForTeam(b.team)}></i>{:else}—{/if}</span
				>
				<span class="meta num">{b ? tyre(b) : ''}&nbsp;</span>
			</div>
		</div>
		<div class="trend">
			{#if a && b && trendValues.length >= 2}
				<Sparkline
					values={trendValues}
					domain={trendDomain}
					bands={trendBands}
					label="The gap between them, lap by lap"
					width={320}
					height={56}
				/>
			{/if}
		</div>
		<p class="line">{a && b ? line : 'Waiting for the running order.'}</p>
	</div>

	<div class="glance-next">
		<div class="caps accent">What could happen next</div>
		<p class="headline">
			{model.headline.before}<em>{model.headline.em}</em>{model.headline.after}
		</p>
		<p class="sub">
			{model.sub.before}{#if model.sub.em}<strong>{model.sub.em}</strong>{/if}{model.sub.after}
			{#if model.confidence}<ConfBadge level={model.confidence} />{/if}
		</p>
	</div>

	<div class="glance-top">
		<div class="top-head">
			<h2>Top five</h2>
			<button type="button" class="all" class:waiting={rows.length === 0} onclick={onAll}
				>All {rows.length}</button
			>
		</div>
		<ol>
			{#each top as row (row.driver)}
				<li>
					<button
						type="button"
						class:focused={row.driver === a?.driver || row.driver === b?.driver}
						aria-label="Watch {names[row.driver] ?? row.code ?? row.driver}'s battle"
						onclick={() => onFocus(row.driver)}
					>
						<span class="pos num">{row.position}</span>
						<i style:background={colorForTeam(row.team)}></i>
						<span class="code">{row.code ?? row.driver}</span>
						<span class="gap-to num">{gap(row)}</span>
						<TyreBadge compound={row.compound} age={row.tyreAge} />
					</button>
				</li>
			{/each}
			{#each Array(Math.max(0, TOP - top.length)) as _, k (k)}
				<li class="placeholder" aria-hidden="true"></li>
			{/each}
		</ol>
	</div>

	<div class="glance-why">
		{#if latest}
			<div class="caps accent">{whyLabel(latest)}</div>
			<p>{latest.text}</p>
		{:else}
			<div class="caps">The latest moment</div>
			<p class="none">Nothing explained yet. Moments appear here as your clock reaches them.</p>
		{/if}
	</div>
</section>

<style>
	.p-glance {
		display: grid;
		gap: 14px;
		padding: 0;
		background: none;
		border: 0;
		box-shadow: none;
	}
	/* A tablet held upright: two columns, so nothing stretches. */
	@media (min-width: 600px) {
		.p-glance {
			grid-template-columns: repeat(2, minmax(0, 1fr));
			align-items: start;
		}
	}
	.glance-battle,
	.glance-next {
		border: 1px solid var(--rule);
		border-radius: var(--radius);
		background: var(--panel);
		padding: 14px 16px;
	}
	.accent {
		color: var(--warn, var(--accent));
	}
	.duel {
		display: grid;
		grid-template-columns: 1fr auto 1fr;
		align-items: center;
		gap: 8px;
		margin-top: 8px;
	}
	.side {
		display: grid;
		gap: 2px;
		min-width: 0;
	}
	.side.right {
		justify-items: end;
		text-align: right;
	}
	.name {
		display: inline-flex;
		align-items: center;
		gap: 7px;
		font: 500 20px / 1.1 var(--font-display);
		color: var(--ink);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.name i,
	li i {
		flex: none;
		width: 4px;
		height: 1em;
		border-radius: 1px;
	}
	.meta {
		font-size: 11px;
		letter-spacing: 0.08em;
		color: var(--muted);
	}
	.gap {
		font: 400 44px / 1 var(--font-display);
		color: var(--ink);
	}
	.gap small {
		font-size: 13px;
		margin-left: 3px;
		color: var(--muted);
	}
	.trend {
		margin-top: 10px;
	}
	.trend :global(.sparkline) {
		width: 100%;
		height: auto;
		display: block;
	}
	.line,
	.sub {
		margin: 8px 0 0;
		font-size: 13px;
		color: var(--muted);
	}
	.sub {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 4px 8px;
	}
	.headline {
		margin: 6px 0 0;
		font: 400 19px / 1.3 var(--font-display);
		color: var(--ink);
	}
	.headline em {
		font-style: normal;
		color: var(--accent);
	}
	.top-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}
	h2 {
		margin: 0;
		font: 500 18px / 1.2 var(--font-display);
		color: var(--ink);
	}
	.all {
		min-height: 44px;
		min-width: 44px;
		padding: 0 4px;
		border: 0;
		background: none;
		color: var(--accent);
		font: 500 13px / 1 var(--font-ui);
		cursor: pointer;
	}
	ol {
		list-style: none;
		margin: 0;
		padding: 0;
		border-top: 1px solid var(--rule);
	}
	li button {
		width: 100%;
		min-height: 44px;
		display: grid;
		grid-template-columns: 22px 4px 1fr auto 52px;
		align-items: center;
		gap: 10px;
		padding: 0 8px;
		border: 0;
		border-bottom: 1px solid var(--rule);
		background: none;
		color: var(--ink);
		font: 500 14px / 1 var(--font-mono);
		text-align: left;
		cursor: pointer;
	}
	li button.focused {
		background: var(--panel);
	}
	li button:focus-visible,
	.all:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: -2px;
	}
	.pos {
		color: var(--muted);
	}
	.gap-to {
		text-align: right;
	}
	.glance-why p {
		margin: 6px 0 0;
		font-size: 14px;
		line-height: 1.45;
		color: var(--ink);
	}
	.trend {
		aspect-ratio: 320 / 56;
	}
	.line {
		min-height: 2.9em;
	}
	.headline {
		min-height: 2.6em;
	}
	.sub {
		min-height: 4.4em;
		align-content: start;
	}
	li.placeholder {
		height: 45px;
		border-bottom: 1px solid var(--rule);
	}
	.all.waiting {
		visibility: hidden;
	}
	.glance-why {
		min-height: 7.5em;
	}
	.glance-why .none {
		color: var(--muted);
	}
</style>
