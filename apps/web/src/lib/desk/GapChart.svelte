<!--
  Gap to the leader: every car, lap by lap, up to the viewer's clock (the
  released laps only). Lower on the chart is further behind; jumps are pit
  stops; gaps past 60 s are pinned to the bottom edge. Safety car and VSC
  laps are shaded, from the laps' own track-status flags.
-->
<script lang="ts">
	import type { components } from '$lib/api/client';
	import { measureWidth, pairColors } from './charts';
	import type { FocusPair } from './focus';
	import { sgn } from './format';
	import { hideTip, showTip } from './tooltip';

	type Lap = components['schemas']['LapRowOut'];

	interface Props {
		laps: Lap[];
		pair: FocusPair | null;
		codes: Record<string, string>;
		teams: Record<string, string | null>;
		totalLaps: number | null;
	}

	let { laps, pair, codes, teams, totalLaps }: Props = $props();
	let width = $state(640);

	const YMAX = 60;
	let dims = $derived({ W: width, H: width < 480 ? 250 : 290, L: 44, R: 44, T: 14, B: 32 });

	/** driver -> lap -> gap to the leader (0 for whoever led that lap). */
	let gaps = $derived.by(() => {
		const out: Record<string, Record<number, number>> = {};
		for (const lap of laps) {
			const gap = lap.position === 1 ? 0 : lap.gap_to_leader_s;
			if (gap == null) continue;
			(out[lap.driver] ??= {})[lap.lap_number] = gap;
		}
		return out;
	});
	const lapsOf = (driver: string): [number, number][] =>
		Object.entries(gaps[driver] ?? {})
			.map(([lap, gap]): [number, number] => [Number(lap), gap])
			.sort((a, b) => a[0] - b[0]);
	const gapAt = (driver: string, lap: number): number | null => gaps[driver]?.[lap] ?? null;
	let lastLap = $derived(Math.max(0, ...laps.map((l) => l.lap_number)));
	let span = $derived(Math.max(2, totalLaps ?? lastLap));
	const x = (lap: number) => dims.L + ((lap - 1) / (span - 1)) * (dims.W - dims.L - dims.R);
	const y = (v: number) => dims.T + (Math.min(v, YMAX) / YMAX) * (dims.H - dims.T - dims.B);

	function path(driver: string): string {
		return lapsOf(driver)
			.map(([lap, gap], i) => `${i === 0 ? 'M' : 'L'}${x(lap).toFixed(1)},${y(gap).toFixed(1)}`)
			.join('');
	}

	/** Contiguous runs of laps any car ran under the safety car or VSC. */
	let neutralised = $derived.by(() => {
		const flagged: Record<number, 'SC' | 'VSC'> = {};
		for (const lap of laps) {
			if (lap.is_sc) flagged[lap.lap_number] = 'SC';
			else if (lap.is_vsc && flagged[lap.lap_number] === undefined) flagged[lap.lap_number] = 'VSC';
		}
		const runs: { from: number; to: number; label: 'SC' | 'VSC' }[] = [];
		for (const lap of Object.keys(flagged)
			.map(Number)
			.sort((a, b) => a - b)) {
			const label = flagged[lap]!;
			const last = runs[runs.length - 1];
			if (last && last.to === lap - 1 && last.label === label) last.to = lap;
			else runs.push({ from: lap, to: lap, label });
		}
		return runs;
	});

	let colors = $derived(
		pair ? pairColors(teams[pair.a] ?? null, teams[pair.b] ?? null) : { a: '', b: '' }
	);
	let ends = $derived.by(() => {
		if (!pair) return [];
		const out = [
			{ driver: pair.b, color: colors.b, shape: 'square' as const },
			{ driver: pair.a, color: colors.a, shape: 'circle' as const }
		]
			.map((s) => {
				const own = lapsOf(s.driver);
				const last = own[own.length - 1];
				if (!last) return null;
				const [lap, gap] = last;
				return { ...s, x: x(lap), y: y(gap), labelY: y(gap) };
			})
			.filter((e): e is NonNullable<typeof e> => e !== null);
		if (out.length === 2 && Math.abs(out[0]!.labelY - out[1]!.labelY) < 14) {
			const [p, q] = out[0]!.labelY <= out[1]!.labelY ? [out[0]!, out[1]!] : [out[1]!, out[0]!];
			p.labelY -= 7;
			q.labelY += 7;
		}
		return out;
	});

	let hoverLap = $state<number | null>(null);
	let svgEl: SVGSVGElement | undefined = $state();

	function onMove(event: PointerEvent): void {
		if (!svgEl || !pair || lastLap < 1) return;
		const rect = svgEl.getBoundingClientRect();
		const vx = ((event.clientX - rect.left) / rect.width) * dims.W;
		const lap = Math.max(
			1,
			Math.min(lastLap, Math.round(1 + ((vx - dims.L) / (dims.W - dims.L - dims.R)) * (span - 1)))
		);
		hoverLap = lap;
		const ga = gapAt(pair.a, lap);
		const gb = gapAt(pair.b, lap);
		showTip(
			`Lap ${lap}`,
			[
				{
					label: codes[pair.a] ?? pair.a,
					value: ga == null ? '—' : `+${ga.toFixed(2)} s`,
					color: colors.a
				},
				{
					label: codes[pair.b] ?? pair.b,
					value: gb == null ? '—' : `+${gb.toFixed(2)} s`,
					color: colors.b
				},
				{
					label: `${codes[pair.a] ?? pair.a} to ${codes[pair.b] ?? pair.b}`,
					value: ga == null || gb == null ? '—' : `${sgn(ga - gb)} s`
				}
			],
			event
		);
	}

	function onLeave(): void {
		hoverLap = null;
		hideTip();
	}

	let tableRows = $derived.by(() => {
		if (!pair) return [];
		const rows: { lap: number; a: number | null; b: number | null }[] = [];
		for (let lap = lastLap; lap >= Math.max(1, lastLap - 9); lap--) {
			rows.push({ lap, a: gapAt(pair.a, lap), b: gapAt(pair.b, lap) });
		}
		return rows;
	});

	const xTickLaps = (last: number) => {
		const step = last > 40 ? 10 : last > 20 ? 5 : 2;
		const out = [1];
		for (let v = step; v < last; v += step) out.push(v);
		out.push(last);
		return out;
	};
</script>

<div class="chart-head">
	<div>
		<h3 class="chart-title">Gap to the leader</h3>
		<p class="chart-cap">
			Every car, lap by lap, up to your clock. Lower on the chart is further behind; the jumps are
			pit stops. Gaps over 60 s are pinned to the bottom edge.
		</p>
	</div>
</div>
{#if pair}
	<div class="legend" style="margin-bottom:8px">
		<span><i class="key-line" style:border-color={colors.a}></i>{codes[pair.a] ?? pair.a}</span>
		<span><i class="key-line" style:border-color={colors.b}></i>{codes[pair.b] ?? pair.b}</span>
		<span><i class="key-line" style="border-color:var(--other-line)"></i>Other cars</span>
	</div>
{/if}
<div class="chart" use:measureWidth={(w) => (width = w)}>
	{#if lastLap < 1}
		<div class="empty">Gaps appear after the first lap.</div>
	{:else}
		<svg
			bind:this={svgEl}
			viewBox="0 0 {dims.W} {dims.H}"
			role="img"
			aria-label="Gap to leader by lap"
		>
			{#each neutralised as run (run.from)}
				<rect
					x={x(Math.max(1, run.from - 0.5))}
					y={dims.T}
					width={Math.max(2, x(Math.min(run.to + 0.5, lastLap)) - x(Math.max(1, run.from - 0.5)))}
					height={dims.H - dims.T - dims.B}
					fill="var(--warn-fill)"
					opacity="0.14"
				/>
				<text
					x={x(Math.max(1, run.from - 0.5)) + 4}
					y={dims.T + 12}
					class="lbl"
					style="fill:var(--warn);font-size:10.5px">{run.label}</text
				>
			{/each}
			{#each [0, 10, 20, 30, 40, 50, 60] as v (v)}
				<line x1={dims.L} x2={dims.W - dims.R} y1={y(v)} y2={y(v)} stroke="var(--grid)" />
				<text x={dims.L - 8} y={y(v) + 4} text-anchor="end"
					>{v === 0 ? '0' : `+${v}`}{v === 60 ? '+' : ''}</text
				>
			{/each}
			{#each xTickLaps(span) as v (v)}
				<text x={x(v)} y={dims.H - dims.B + 18} text-anchor="middle">{v}</text>
			{/each}
			<text
				x={(dims.L + dims.W - dims.R) / 2}
				y={dims.H - 2}
				text-anchor="middle"
				class="lbl"
				style="fill:var(--muted)">Lap</text
			>
			<line
				x1={x(lastLap)}
				x2={x(lastLap)}
				y1={dims.T}
				y2={dims.H - dims.B}
				stroke="var(--rule-strong)"
			/>
			<text
				x={x(lastLap) + 5}
				y={dims.H - dims.B - 6}
				class="lbl"
				style="fill:var(--muted);font-size:10.5px">your clock</text
			>
			{#each Object.keys(gaps) as driver (driver)}
				{#if !pair || (driver !== pair.a && driver !== pair.b)}
					<path
						d={path(driver)}
						fill="none"
						stroke="var(--other-line)"
						stroke-width="1.25"
						stroke-linejoin="round"
					/>
				{/if}
			{/each}
			{#each ends as e (e.driver)}
				<path
					d={path(e.driver)}
					fill="none"
					stroke={e.color}
					stroke-width="2.25"
					stroke-linejoin="round"
					stroke-linecap="round"
				/>
				{#if e.shape === 'circle'}
					<circle cx={e.x} cy={e.y} r="4.5" fill={e.color} stroke="var(--panel)" stroke-width="2" />
				{:else}
					<rect
						x={e.x - 4.5}
						y={e.y - 4.5}
						width="9"
						height="9"
						rx="1.5"
						fill={e.color}
						stroke="var(--panel)"
						stroke-width="2"
					/>
				{/if}
				<text x={e.x + 9} y={e.labelY + 4} class="lbl">{codes[e.driver] ?? e.driver}</text>
			{/each}
			{#if hoverLap !== null}
				<line
					x1={x(hoverLap)}
					x2={x(hoverLap)}
					y1={dims.T}
					y2={dims.H - dims.B}
					stroke="var(--ink-2)"
					stroke-width="1"
				/>
			{/if}
			<rect
				role="presentation"
				x={dims.L}
				y={dims.T}
				width={dims.W - dims.L - dims.R}
				height={dims.H - dims.T - dims.B}
				fill="transparent"
				onpointermove={onMove}
				onpointerleave={onLeave}
			/>
		</svg>
	{/if}
</div>
{#if pair && tableRows.length}
	<details class="data">
		<summary>Show as table</summary>
		<div class="table-scroll">
			<table class="data-t">
				<thead>
					<tr>
						<th>Lap</th>
						<th>{codes[pair.a] ?? pair.a} gap</th>
						<th>{codes[pair.b] ?? pair.b} gap</th>
						<th>{codes[pair.a] ?? pair.a} to {codes[pair.b] ?? pair.b}</th>
					</tr>
				</thead>
				<tbody>
					{#each tableRows as r (r.lap)}
						<tr>
							<td>Lap {r.lap}</td>
							<td class="num">{r.a == null ? '—' : `+${r.a.toFixed(2)}`}</td>
							<td class="num">{r.b == null ? '—' : `+${r.b.toFixed(2)}`}</td>
							<td class="num">{r.a == null || r.b == null ? '—' : sgn(r.a - r.b)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
{/if}
