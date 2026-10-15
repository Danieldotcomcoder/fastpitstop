<!--
  The battle that matters (mockup A, ROADMAP W2 D3): the desktop desk's hero.
  The two cars, the gap between them at the line, how that gap moved over
  the last laps (neutralised laps shaded, so a gap that closed behind the
  safety car never reads as pace), and one plain sentence saying which it
  was. Everything comes from the released tower and race history.
-->
<script lang="ts">
	import RollNumber from '$lib/ui/RollNumber.svelte';
	import { colorForTeam } from '$lib/teams';
	import { compoundName, surname } from './format';
	import { battleVerdict, type BattleTrend } from './hero';
	import type { BoardDriver } from './pit-board';

	interface Props {
		attacker: BoardDriver | null;
		defender: BoardDriver | null;
		/** The attacker's interval to the defender, from the tower. */
		gapS: number | null;
		trend: BattleTrend | null;
		names: Record<string, string>;
		/** Second car of its team, drawn dashed (W2 D1). */
		dashed: Record<string, boolean>;
	}

	let { attacker, defender, gapS, trend, names, dashed }: Props = $props();

	const who = (d: BoardDriver) => surname(names[d.driver]) ?? d.code;
	const meta = (d: BoardDriver) =>
		[
			d.position !== null ? `P${d.position}` : null,
			d.team,
			d.compound ? `${compoundName(d.compound).toLowerCase()}, ${d.tyreAge ?? '?'} laps` : null
		]
			.filter(Boolean)
			.join(' · ');

	let sentence = $derived(
		attacker && defender
			? battleVerdict(trend, attacker, defender, who(attacker), who(defender))
			: ''
	);

	// --- the trend ----------------------------------------------------------
	const W = 640;
	const H = 92;
	const PAD = { l: 6, r: 56, t: 10, b: 18 };
	let pts = $derived(trend?.points ?? []);
	let lo = $derived(pts.length ? pts[0]!.lap : 0);
	let hi = $derived(pts.length ? pts[pts.length - 1]!.lap : 1);
	let top = $derived(Math.max(0.5, ...pts.map((p) => p.gapS)) * 1.08);
	const x = (lap: number) => PAD.l + ((lap - lo) / Math.max(1, hi - lo)) * (W - PAD.l - PAD.r);
	const y = (gap: number) => PAD.t + (1 - Math.max(0, gap) / top) * (H - PAD.t - PAD.b);
	let line = $derived(pts.map((p) => `${x(p.lap).toFixed(1)},${y(p.gapS).toFixed(1)}`).join(' '));
	/** A neutralised lap shades the stretch that ended on it. */
	let shades = $derived(
		pts.flatMap((p, i) =>
			i > 0 && p.neutral !== null ? [{ x0: x(pts[i - 1]!.lap), x1: x(p.lap), key: p.lap }] : []
		)
	);
	let last = $derived(pts[pts.length - 1] ?? null);
	/** The gap rolls once a lap, as the pair crosses the line (W2 D4). */
	let lapKey = $derived(last?.lap ?? null);
</script>

<section class="panel p-battle" aria-labelledby="battleTitle" data-testid="battle-hero">
	<div class="panel-head">
		<h2 class="caps battle-kicker" id="battleTitle">
			The battle that matters{#if defender?.position}&nbsp;· for P{defender.position}{/if}
		</h2>
		<span class="battle-hint">Tap any driver in Timing to watch their fight instead</span>
	</div>
	<!-- Every block keeps its size from the first paint, filled or not: the
	     card leads the desk, and growing when the running order arrived
	     would push every panel under it down (W0's layout-shift budget). -->
	<div class="duel">
		<div class="side">
			<span
				class="bar"
				class:dashed={attacker !== null && dashed[attacker.driver]}
				style:--team={attacker ? colorForTeam(attacker.team) : 'var(--rule)'}
			></span>
			<div>
				<div class="name">{attacker ? who(attacker) : '—'}</div>
				<div class="meta">{attacker ? meta(attacker) : ''}&nbsp;</div>
			</div>
		</div>
		<div class="gap">
			<div class="gap-num num" data-testid={attacker && defender ? 'battle-gap' : undefined}>
				{#if attacker && defender && gapS !== null}<RollNumber
						value={gapS.toFixed(2)}
						key={lapKey}
					/><small> s</small>{:else}—{/if}
			</div>
			<div class="caps">Gap at the line</div>
		</div>
		<div class="side right">
			<div>
				<div class="name">{defender ? who(defender) : '—'}</div>
				<div class="meta">{defender ? meta(defender) : ''}&nbsp;</div>
			</div>
			<span
				class="bar"
				class:dashed={defender !== null && dashed[defender.driver]}
				style:--team={defender ? colorForTeam(defender.team) : 'var(--rule)'}
			></span>
		</div>
	</div>
	<div class="trend">
		{#if attacker && defender && pts.length >= 2 && last}
			<svg
				viewBox="0 0 {W} {H}"
				role="img"
				aria-label={`Gap from ${who(attacker)} to ${who(defender)}, laps ${lo} to ${hi}: ${pts[0]!.gapS.toFixed(1)} seconds to ${last.gapS.toFixed(1)}`}
			>
				{#each shades as s (s.key)}
					<rect
						x={s.x0}
						y="0"
						width={Math.max(1, s.x1 - s.x0)}
						height={H - PAD.b}
						class="neutral"
					/>
				{/each}
				<line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} class="axis" />
				<polyline points={line} class="gapline" />
				<circle cx={x(last.lap)} cy={y(last.gapS)} r="4.5" class="gapdot" />
				<text x={x(last.lap) + 10} y={y(last.gapS) + 4} class="endlabel"
					>{last.gapS.toFixed(2)} s</text
				>
				<text x={PAD.l} y={H - 4} class="lap">Lap {lo}</text>
				<text x={W - PAD.r} y={H - 4} class="lap" text-anchor="end">Lap {hi}</text>
			</svg>
		{:else}
			<p class="trend-wait">
				{attacker && defender
					? 'The trend appears once both cars have finished two laps on your clock.'
					: 'Waiting for the running order.'}
			</p>
		{/if}
	</div>
	<p class="verdict" data-testid="battle-verdict">{sentence}</p>
</section>

<style>
	.p-battle {
		display: grid;
		gap: 12px;
	}
	/* Two fixed lines: the kicker gains "for P4" when the running order
	   arrives, and a wrapping header moved every panel below (W0). */
	.p-battle .panel-head {
		margin-bottom: 0;
		flex-direction: column;
		flex-wrap: nowrap;
		align-items: flex-start;
		gap: 2px;
	}
	.battle-kicker,
	.battle-hint {
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		max-width: 100%;
	}
	.battle-kicker {
		margin: 0;
		color: var(--muted);
	}
	.battle-hint {
		font-size: 12px;
		color: var(--muted);
	}
	/* A fixed centre: the gap's digits change width as they roll, and an
	   "auto" column nudged both drivers sideways every lap. */
	.duel {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 11.5rem minmax(0, 1fr);
		align-items: center;
		gap: 16px;
	}
	.side {
		display: flex;
		align-items: center;
		gap: 12px;
		min-width: 0;
	}
	.side.right {
		justify-content: flex-end;
		text-align: right;
	}
	.bar {
		flex: none;
		width: 6px;
		height: 44px;
		border-radius: 3px;
		background: var(--team);
	}
	.bar.dashed {
		background: repeating-linear-gradient(180deg, var(--team) 0 6px, transparent 6px 10px);
		box-shadow: inset 0 0 0 1px var(--team);
	}
	.name {
		font: 500 30px / 1.05 var(--f-display);
		letter-spacing: -0.01em;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.meta {
		margin-top: 4px;
		font: 400 12px / 1.3 var(--f-mono);
		color: var(--muted);
		/* Two lines held, filled or not. */
		min-height: 2.6em;
	}
	.gap {
		text-align: center;
	}
	.gap-num {
		font: 500 64px / 1 var(--f-display);
		letter-spacing: -0.02em;
	}
	.gap-num small {
		font-size: 22px;
		color: var(--muted);
	}
	.gap .caps {
		margin-top: 4px;
		color: var(--muted);
	}
	/* The trend's box has the chart's own shape from the first paint. */
	.trend {
		aspect-ratio: 640 / 92;
		display: flex;
		align-items: center;
	}
	.trend svg {
		display: block;
		width: 100%;
		height: auto;
	}
	.neutral {
		fill: color-mix(in srgb, var(--status-sc) 18%, transparent);
	}
	.axis {
		stroke: var(--rule);
	}
	.gapline {
		fill: none;
		stroke: var(--accent);
		stroke-width: 2.5;
		stroke-linejoin: round;
		stroke-linecap: round;
	}
	.gapdot {
		fill: var(--accent);
	}
	.endlabel,
	.lap {
		font: 400 11px var(--f-mono);
		fill: var(--muted);
	}
	.endlabel {
		fill: var(--ink);
	}
	.verdict {
		margin: 0;
		/* Two lines held: the sentence arrives with the trend. */
		min-height: 2.9em;
		font: 400 16px / 1.45 var(--f-sans);
		color: var(--ink);
		max-width: 70ch;
	}
	.trend-wait {
		margin: 0;
		color: var(--muted);
		font-size: 13px;
	}
</style>
