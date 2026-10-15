<!--
  Undercut watch: the design's dark pit board, the one loud object on the
  page. Reads a `BoardModel` (see pit-board.ts): every number is the
  kernel's own crossover, released on the viewer's clock.
-->
<script lang="ts">
	import { colorForTeam } from '$lib/teams';
	import ConfBadge from './ConfBadge.svelte';
	import { sgn, surname } from './format';
	import { modelRecordLine } from '$lib/model-record';
	import { tyresLine, type BoardModel } from './pit-board';
	import type { SparkPoint } from './snapshot-history';
	import { hideTip, showTip } from './tooltip';

	interface Props {
		model: BoardModel;
		spark: SparkPoint[];
		focusName: string | null;
	}

	let { model, spark, focusName }: Props = $props();

	let hasNumbers = $derived(model.marginS !== null && model.state !== 'pitting');
	let a = $derived(model.attacker);
	let b = $derived(model.defender);

	// --- the 95% interval bar ------------------------------------------------
	const CI = { W: 360, H: 74, pad: 14 };
	let ciDomain = $derived.by(() => {
		const low = model.lowS ?? model.marginS ?? 0;
		const high = model.highS ?? model.marginS ?? 0;
		return {
			lo: Math.min(-3, Math.floor(low - 0.5)),
			hi: Math.max(3, Math.ceil(high + 0.5))
		};
	});
	const cx = (v: number) =>
		CI.pad + ((v - ciDomain.lo) / (ciDomain.hi - ciDomain.lo)) * (CI.W - 2 * CI.pad);
	let ciTicks = $derived.by(() => {
		const out: number[] = [];
		const step = ciDomain.hi - ciDomain.lo > 8 ? 2 : 1;
		for (let v = ciDomain.lo; v <= ciDomain.hi; v++)
			if ((v - ciDomain.lo) % step === 0) out.push(v);
		return out;
	});
	const tickLabel = (v: number) => (v === 0 ? '0' : `${v > 0 ? '+' : '−'}${Math.abs(v)}`);

	// --- interval against the undercut line -----------------------------------
	const SP = { W: 360, H: 96, L: 8, R: 92, T: 8, B: 20 };
	let sparkReady = $derived(spark.filter((p) => p.gapS !== null || p.lineS !== null).length >= 2);
	let sparkDomain = $derived.by(() => {
		const values = spark.flatMap((p) => [p.gapS, p.lineS]).filter((v): v is number => v !== null);
		if (values.length === 0) return { lo: 0, hi: 1 };
		// No floor at zero: a negative break-even line (the stop does not pay
		// even from a standing start) is a real answer and must stay on the chart.
		const lo = Math.floor((Math.min(...values) - 0.3) * 2) / 2;
		const hi = Math.ceil((Math.max(...values) + 0.3) * 2) / 2;
		return { lo, hi: hi > lo ? hi : lo + 1 };
	});
	let firstLap = $derived(spark[0]?.lap ?? 0);
	let lastLap = $derived(spark[spark.length - 1]?.lap ?? 0);
	const sx = (lap: number) =>
		SP.L + ((lap - firstLap) / Math.max(1, lastLap - firstLap)) * (SP.W - SP.L - SP.R);
	const sy = (v: number) =>
		SP.T + (1 - (v - sparkDomain.lo) / (sparkDomain.hi - sparkDomain.lo)) * (SP.H - SP.T - SP.B);

	function sparkPath(key: 'gapS' | 'lineS'): string {
		let d = '';
		let pen = false;
		for (const p of spark) {
			const v = p[key];
			if (v === null) {
				pen = false;
				continue;
			}
			d += `${pen ? 'L' : 'M'}${sx(p.lap).toFixed(1)},${sy(v).toFixed(1)}`;
			pen = true;
		}
		return d;
	}
	let shades = $derived.by(() => {
		const width = (SP.W - SP.L - SP.R) / Math.max(1, spark.length - 1);
		return spark
			.filter((p) => p.gapS !== null && p.lineS !== null && p.gapS < p.lineS)
			.map((p) => ({ x: sx(p.lap) - width / 2, width }));
	});
	let endLabels = $derived.by(() => {
		const lastGap = [...spark].reverse().find((p) => p.gapS !== null);
		const lastLine = [...spark].reverse().find((p) => p.lineS !== null);
		if (!lastGap || !lastLine || lastGap.gapS === null || lastLine.lineS === null) return null;
		let gy = sy(lastGap.gapS);
		let ly = sy(lastLine.lineS);
		if (Math.abs(gy - ly) < 14) {
			if (gy <= ly) {
				gy -= 7;
				ly += 7;
			} else {
				gy += 7;
				ly -= 7;
			}
		}
		return {
			gap: { x: sx(lastGap.lap) + 8, y: gy, text: `gap ${lastGap.gapS.toFixed(2)}` },
			line: { x: sx(lastLine.lap) + 8, y: ly, text: `line ${lastLine.lineS.toFixed(2)}` },
			dot: { x: sx(lastGap.lap), y: sy(lastGap.gapS) }
		};
	});

	let hoverX = $state<number | null>(null);
	let svgEl: SVGSVGElement | undefined = $state();

	function onSparkMove(event: PointerEvent): void {
		if (!svgEl || spark.length === 0) return;
		const rect = svgEl.getBoundingClientRect();
		const vx = ((event.clientX - rect.left) / rect.width) * SP.W;
		const span = (SP.W - SP.L - SP.R) / Math.max(1, spark.length - 1);
		const k = Math.max(0, Math.min(spark.length - 1, Math.round((vx - SP.L) / span)));
		const p = spark[k];
		if (!p) return;
		hoverX = sx(p.lap);
		const style = getComputedStyle(svgEl);
		showTip(
			`Lap ${p.lap}`,
			[
				{
					label: 'Interval',
					value: p.gapS === null ? 'pit lap' : `${p.gapS.toFixed(2)} s`,
					color: style.getPropertyValue('--board-ink').trim()
				},
				{
					label: 'Undercut line',
					value: p.lineS === null ? '—' : `${p.lineS.toFixed(2)} s`,
					color: style.getPropertyValue('--board-hi').trim(),
					dashed: true
				}
			],
			event
		);
	}

	function onSparkLeave(): void {
		hoverX = null;
		hideTip();
	}

	function rejoinNote(): string {
		const r = model.rejoin;
		if (!r || r.behind === null || r.gapS === null) return 'Clear air';
		const code = r.behind;
		return `${r.gapS.toFixed(1)} s behind ${code}, ${r.inTraffic ? 'in traffic' : 'clear air'}`;
	}
</script>

<section
	class="panel p-undercut"
	aria-labelledby="ucTitle"
	id="undercut"
	data-testid="undercut-panel"
>
	<div class="board-top">
		<h2 class="caps" id="ucTitle" style="margin:0">What could happen next</h2>
		{#if model.confidence && hasNumbers}<ConfBadge level={model.confidence} />{/if}
	</div>
	{#if a && b}
		<div class="pair">
			<span class="who"><i style:background={colorForTeam(a.team)}></i>{a.code}</span><span
				>P{a.position ?? '—'}</span
			>
			<span class="arrow" aria-label="attacking">→</span>
			<span class="who"><i style:background={colorForTeam(b.team)}></i>{b.code}</span><span
				>P{b.position ?? '—'}</span
			>
		</div>
		<div class="tyres-line">{tyresLine(model)}</div>
	{:else}
		<!-- The pair's lines, held empty until the running order arrives, so
		     the call under them never jumps down (W0's layout-shift budget). -->
		<div class="pair" aria-hidden="true">&nbsp;</div>
		<div class="tyres-line" aria-hidden="true">&nbsp;</div>
	{/if}
	<p class="headline">{model.headline.before}<em>{model.headline.em}</em>{model.headline.after}</p>
	<p class="board-sub">
		{model.sub.before}{#if model.sub.em}<strong>{model.sub.em}</strong>{/if}{model.sub.after}
	</p>

	{#if a && b}
		<div class="board-grid">
			<div>
				{#if hasNumbers && model.marginS !== null}
					<div class="ci-wrap">
						<svg
							viewBox="0 0 {CI.W} {CI.H}"
							role="img"
							aria-label={`Margin after both stops ${sgn(model.marginS)} seconds${model.lowS !== null && model.highS !== null ? `, 95 percent interval ${sgn(model.lowS)} to ${sgn(model.highS)}` : ''}`}
						>
							<text
								x={CI.pad}
								y="10"
								fill="var(--board-muted)"
								font-family="var(--f-sans)"
								font-size="11">← comes out behind</text
							>
							<text
								x={CI.W - CI.pad}
								y="10"
								text-anchor="end"
								fill="var(--board-muted)"
								font-family="var(--f-sans)"
								font-size="11">comes out ahead →</text
							>
							<line
								x1={CI.pad}
								x2={CI.W - CI.pad}
								y1="50"
								y2="50"
								stroke="var(--board-rule)"
								stroke-width="1"
							/>
							{#if model.lowS !== null && model.highS !== null}
								<rect
									x={cx(model.lowS)}
									y="22"
									width={Math.max(2, cx(model.highS) - cx(model.lowS))}
									height="20"
									rx="3"
									fill="var(--board-band)"
								/>
							{/if}
							<line
								x1={cx(0)}
								x2={cx(0)}
								y1="16"
								y2="50"
								stroke="var(--board-ink)"
								stroke-width="1.5"
							/>
							<circle
								cx={cx(model.marginS)}
								cy="32"
								r="6.5"
								fill="var(--board-hi)"
								stroke="var(--board)"
								stroke-width="2"
							/>
							{#each ciTicks as v (v)}
								<line
									x1={cx(v)}
									x2={cx(v)}
									y1="50"
									y2="54"
									stroke="var(--board-muted)"
									stroke-width="1"
								/>
								<text
									x={cx(v)}
									y="67"
									text-anchor="middle"
									fill="var(--board-muted)"
									font-family="var(--f-mono)"
									font-size="11">{tickLabel(v)}</text
								>
							{/each}
						</svg>
					</div>
				{/if}
				{#if hasNumbers && sparkReady}
					<div class="spark-wrap">
						<div class="caps" style="margin-bottom:6px">
							Interval against the undercut line, last laps
						</div>
						<svg
							bind:this={svgEl}
							viewBox="0 0 {SP.W} {SP.H}"
							role="img"
							aria-label="Interval versus undercut line over the last laps"
						>
							{#each shades as shade, index (index)}
								<rect
									x={shade.x.toFixed(1)}
									y={SP.T}
									width={shade.width.toFixed(1)}
									height={SP.H - SP.T - SP.B}
									fill="var(--board-band)"
								/>
							{/each}
							<line
								x1={SP.L}
								x2={SP.W - SP.R}
								y1={SP.H - SP.B}
								y2={SP.H - SP.B}
								stroke="var(--board-rule)"
							/>
							<path
								d={sparkPath('lineS')}
								fill="none"
								stroke="var(--board-hi)"
								stroke-width="2"
								stroke-dasharray="4 3"
								stroke-linecap="round"
							/>
							<path
								d={sparkPath('gapS')}
								fill="none"
								stroke="var(--board-ink)"
								stroke-width="2"
								stroke-linejoin="round"
								stroke-linecap="round"
							/>
							{#if endLabels}
								<circle
									cx={endLabels.dot.x}
									cy={endLabels.dot.y}
									r="4"
									fill="var(--board-ink)"
									stroke="var(--board)"
									stroke-width="2"
								/>
								<text
									x={endLabels.gap.x}
									y={endLabels.gap.y + 4}
									fill="var(--board-ink)"
									font-family="var(--f-mono)"
									font-size="11">{endLabels.gap.text}</text
								>
								<text
									x={endLabels.line.x}
									y={endLabels.line.y + 4}
									fill="var(--board-hi)"
									font-family="var(--f-mono)"
									font-size="11">{endLabels.line.text}</text
								>
							{/if}
							<text
								x={SP.L}
								y={SP.H - 5}
								fill="var(--board-muted)"
								font-family="var(--f-mono)"
								font-size="11">L{firstLap}</text
							>
							<text
								x={SP.W - SP.R}
								y={SP.H - 5}
								text-anchor="end"
								fill="var(--board-muted)"
								font-family="var(--f-mono)"
								font-size="11">L{lastLap}</text
							>
							{#if hoverX !== null}
								<line
									x1={hoverX}
									x2={hoverX}
									y1={SP.T}
									y2={SP.H - SP.B}
									stroke="var(--board-muted)"
									stroke-width="1"
								/>
							{/if}
							<rect
								x={SP.L}
								y="0"
								width={SP.W - SP.L - SP.R}
								height={SP.H}
								fill="transparent"
								role="presentation"
								onpointermove={onSparkMove}
								onpointerleave={onSparkLeave}
							/>
						</svg>
						<div class="spark-legend">
							<span><i style="border-color:var(--board-ink)"></i>Interval {a.code} to {b.code}</span
							>
							<span
								><i style="border-color:var(--board-hi);border-top-style:dashed"></i>Undercut line
								(below it, the stop pays)</span
							>
						</div>
					</div>
				{/if}
			</div>
			<dl class="stats">
				{#if hasNumbers && model.marginS !== null}
					<div class="stat">
						<dt>Net margin</dt>
						<dd class:hi={model.marginS > 0}>{sgn(model.marginS)} s</dd>
						{#if model.lowS !== null && model.highS !== null}<span class="note num"
								>95%: {sgn(model.lowS)} to {sgn(model.highS)} s</span
							>{/if}
					</div>
					<div class="stat">
						<dt>Rejoins</dt>
						<dd>{model.rejoin ? `P${model.rejoin.position}` : '—'}</dd>
						<span class="note">{model.rejoin ? rejoinNote() : 'Needs the gap to the leader'}</span>
					</div>
					<div class="stat">
						<dt>Box at end of</dt>
						<dd class:hi={model.state === 'open' || model.state === 'opens'}>
							{model.boxAtLap !== null ? `Lap ${model.boxAtLap}` : '—'}
						</dd>
						<span class="note"
							>{model.boxAtLap !== null ? 'Earliest lap that pays' : 'No window in sight'}</span
						>
					</div>
				{:else}
					<div class="stat">
						<dt>Interval</dt>
						<dd>{model.intervalS !== null ? `+${model.intervalS.toFixed(2)} s` : '—'}</dd>
						<span class="note">{a.code} to {b.code}</span>
					</div>
				{/if}
				<div class="stat">
					<dt>Pit loss</dt>
					<dd>{model.pitLossS !== null ? `${model.pitLossS.toFixed(1)} s` : '—'}</dd>
					<span class="note"
						>{model.pitLossS !== null
							? model.pitLossNote
							: 'Arrives with the first projection'}</span
					>
				</div>
			</dl>
		</div>
	{/if}
	<p class="board-foot">
		{#if hasNumbers && b}Assumes {b.code} covers one lap later. Refit every lap on the laps you’ve seen.{/if}{#if focusName}{hasNumbers
				? ' '
				: ''}Following {surname(focusName) ?? focusName}, tap any driver in Timing to change.{:else}{hasNumbers
				? ' '
				: ''}Tap any driver in Timing to follow their battle.{/if}
	</p>
	<p class="board-record" data-testid="model-record">{modelRecordLine()}</p>
</section>
