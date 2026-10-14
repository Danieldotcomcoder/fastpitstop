<!--
  The race ribbon, "How the top ten got here" (mockup A, ROADMAP W2 D3):
  every current top-ten car's position at the end of each lap so far,
  growing lap by lap as the viewer's clock releases them. Laps run behind a
  safety car, VSC or red flag are shaded, so a cluster of crossing lines
  has its reason drawn under it; teammates differ by a dashed line, not only
  by colour; the focused battle is drawn heavier.

  The x-axis runs to the race distance only when the feed announced it (a
  live race does); otherwise it ends at the lap reached, since a replay's
  finishing lap count would give a shortened race away.
-->
<script lang="ts">
	import { colorForTeam } from '$lib/teams';
	import type { FocusPair } from './focus';
	import type { RibbonModel } from './hero';

	interface Props {
		model: RibbonModel;
		pair: FocusPair | null;
	}

	let { model, pair }: Props = $props();

	const W = 820;
	const H = 262;
	const PAD = { l: 8, r: 72, t: 10, b: 24 };
	let plotRight = $derived(W - PAD.r);
	const x = (lap: number) => PAD.l + (lap / Math.max(1, model.axisEnd)) * (plotRight - PAD.l);
	const y = (position: number) =>
		PAD.t + ((position - 1) / Math.max(1, model.fieldSize - 1)) * (H - PAD.t - PAD.b);

	let toGo = $derived(model.axisEnd > model.reached ? model.axisEnd - model.reached : 0);
	const BAND_LABEL = { sc: 'SC', vsc: 'VSC', red: 'RED' } as const;

	function path(points: { lap: number; position: number }[]): string {
		return points.map((p) => `${x(p.lap).toFixed(1)},${y(p.position).toFixed(1)}`).join(' ');
	}
	const focused = (driver: string) => pair !== null && (driver === pair.a || driver === pair.b);
	/** Heavier lines on top. */
	let ordered = $derived(
		[...model.lines].sort((a, b) => Number(focused(a.driver)) - Number(focused(b.driver)))
	);
	let labelX = $derived(x(model.reached) + 8);
	let summary = $derived(
		model.lines.length === 0
			? 'No laps completed yet on your clock.'
			: `Positions of the current top ${model.lines.length} at the end of each of the ${model.reached} laps your clock has reached.`
	);
</script>

<section class="panel p-ribbon" aria-labelledby="ribbonTitle" data-testid="race-ribbon">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="ribbonTitle">How the top ten got here</h2>
			<p class="panel-sub">
				Position at the end of every lap, up to the lap your clock has reached
			</p>
		</div>
		<span class="caps ribbon-count" data-testid="ribbon-reached"
			>{#if toGo > 0}{toGo} lap{toGo === 1 ? '' : 's'} to go{:else}Lap {model.reached}{/if}</span
		>
	</div>
	<svg viewBox="0 0 {W} {H}" role="img" aria-label={summary} data-reached={model.reached}>
		{#if toGo > 0}
			<rect
				x={x(model.reached)}
				y="0"
				width={plotRight - x(model.reached)}
				height={H - PAD.b}
				class="ahead"
			/>
			<line x1={x(model.reached)} x2={x(model.reached)} y1="0" y2={H - PAD.b} class="now" />
		{/if}
		{#each model.bands as band (band.fromLap)}
			{@const x0 = x(band.fromLap - 1)}
			{@const x1 = Math.min(plotRight, x(Math.min(band.toLap, model.reached + 1)))}
			<rect
				x={x0}
				y="0"
				width={Math.max(2, x1 - x0)}
				height={H - PAD.b}
				class="band {band.status}"
			/>
			<text x={x0 + 2} y={H - 8} class="axis-label">{BAND_LABEL[band.status]}</text>
		{/each}
		{#each ordered as line (line.driver)}
			{#if line.points.length > 1}
				<polyline
					points={path(line.points)}
					class="line"
					class:focus={focused(line.driver)}
					style:stroke={colorForTeam(line.team)}
					style:stroke-dasharray={line.dashed ? 'var(--teammate-dash)' : null}
				/>
			{/if}
			{#each line.pitLaps.filter((l) => l <= model.reached) as lap (lap)}
				{@const at = line.points.find((p) => p.lap === lap)}
				{#if at}
					<circle
						cx={x(at.lap)}
						cy={y(at.position)}
						r="3"
						class="pit"
						style:stroke={colorForTeam(line.team)}
					/>
				{/if}
			{/each}
		{/each}
		{#each model.lines as line (line.driver)}
			<text x={labelX} y={y(line.position) + 4} class="label" class:focus={focused(line.driver)}
				>{line.position} {line.code}</text
			>
		{/each}
		<text x={PAD.l} y={H - 8} class="axis-label">START</text>
		{#if toGo > 0}
			<text x={plotRight} y={H - 8} class="axis-label" text-anchor="end">LAP {model.axisEnd}</text>
		{/if}
	</svg>
	<p class="ribbon-key">
		<span><i class="k-pit"></i>Pit stop</span>
		<span><i class="k-band"></i>Safety car or VSC</span>
		<span><i class="k-dash"></i>Dashed: second car of the team</span>
	</p>
</section>

<style>
	.p-ribbon svg {
		display: block;
		width: 100%;
		height: auto;
	}
	.ribbon-count {
		color: var(--muted);
	}
	.ahead {
		fill: var(--panel-sunk);
	}
	.now {
		stroke: var(--rule-strong);
		stroke-dasharray: 3 3;
	}
	.band {
		fill: color-mix(in srgb, var(--status-sc) 16%, transparent);
	}
	.band.red {
		fill: color-mix(in srgb, var(--status-red) 16%, transparent);
	}
	.line {
		fill: none;
		stroke-width: 1.75;
		stroke-linejoin: round;
		opacity: 0.85;
	}
	.line.focus {
		stroke-width: 3.25;
		opacity: 1;
	}
	.pit {
		fill: var(--panel);
		stroke-width: 1.5;
	}
	.label {
		font: 400 11px var(--f-mono);
		fill: var(--ink-2);
	}
	.label.focus {
		fill: var(--ink);
		font-weight: 600;
	}
	.axis-label {
		font: 400 10px var(--f-mono);
		fill: var(--muted);
		letter-spacing: 0.06em;
	}
	.ribbon-key {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 16px;
		margin: 8px 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.ribbon-key i {
		display: inline-block;
		margin-right: 6px;
		vertical-align: middle;
	}
	.k-pit {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		border: 1.5px solid var(--ink-2);
	}
	.k-band {
		width: 14px;
		height: 10px;
		background: color-mix(in srgb, var(--status-sc) 30%, transparent);
	}
	.k-dash {
		width: 18px;
		border-top: 2px dashed var(--ink-2);
	}
</style>
