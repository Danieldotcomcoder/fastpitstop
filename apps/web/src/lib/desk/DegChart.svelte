<!--
  Tyre degradation, current stints: the focused pair's fuel-corrected lap
  times against tyre age, with the kernel's own fit refit every lap on the
  laps the viewer has seen (each `kernel_snapshot`'s stint state). Points
  are the released laps from `/laps`, gated by the reveal tracker; the fit
  and the points are in the same fuel-corrected units.
-->
<script lang="ts">
	import type { components } from '$lib/api/client';
	import ConfBadge from './ConfBadge.svelte';
	import { measureWidth, niceTicks, pairColors } from './charts';
	import type { FocusPair } from './focus';
	import { compoundName, fmtLap, sgn } from './format';
	import type { Crossover } from './pit-board';
	import { hideTip, showTip } from './tooltip';

	type Lap = components['schemas']['LapRowOut'];

	interface Props {
		laps: Lap[];
		pair: FocusPair | null;
		crossover: Crossover | null;
		codes: Record<string, string>;
		teams: Record<string, string | null>;
	}

	let { laps, pair, crossover, codes, teams }: Props = $props();
	let width = $state(640);

	/** Off-scale by construction (a standing start, a pit lane, a
	 * neutralised lap): left off the chart rather than squashing it. */
	const OFF_SCALE = new Set(['is_lap1', 'is_in_lap', 'is_out_lap', 'is_sc', 'is_vsc', 'is_red']);
	const REASON: Record<string, string> = {
		is_dirty_air: 'Dirty air',
		is_anomalous: 'Outlier',
		is_yellow: 'Yellow flag'
	};

	interface Point {
		lap: number;
		age: number;
		y: number;
		reason: string | null;
	}

	interface Series {
		driver: string;
		code: string;
		color: string;
		shape: 'circle' | 'square';
		stint: number | null;
		compound: string | null;
		clean: Point[];
		excluded: Point[];
		slope: number | null;
		intercept: number | null;
		reliability: string;
		nFit: number;
	}

	let series = $derived.by((): Series[] => {
		if (!pair) return [];
		const matches = crossover && crossover.attacker === pair.a && crossover.defender === pair.b;
		const colors = pairColors(teams[pair.a] ?? null, teams[pair.b] ?? null);
		return [pair.a, pair.b].map((driver, index) => {
			const state = matches
				? index === 0
					? crossover!.attacker_state
					: crossover!.defender_state
				: null;
			const own = laps.filter(
				(l) => l.driver === driver && l.lap_time_fuel_corrected_s != null && l.tyre_age != null
			);
			const latestStint = own.reduce<number | null>(
				(max, l) =>
					l.stint_number != null && (max === null || l.stint_number > max) ? l.stint_number : max,
				null
			);
			const stint = state?.stint_number ?? latestStint;
			const stintLaps = own.filter((l) => l.stint_number === stint);
			const toPoint = (l: Lap): Point => ({
				lap: l.lap_number,
				age: l.tyre_age as number,
				y: l.lap_time_fuel_corrected_s as number,
				reason: l.is_clean ? null : (REASON[l.exclusion_reasons[0] ?? ''] ?? 'Excluded')
			});
			return {
				driver,
				code: codes[driver] ?? driver,
				color: index === 0 ? colors.a : colors.b,
				shape: index === 0 ? 'circle' : 'square',
				stint,
				compound: state?.compound ?? stintLaps[stintLaps.length - 1]?.compound ?? null,
				clean: stintLaps.filter((l) => l.is_clean).map(toPoint),
				excluded: stintLaps
					.filter((l) => !l.is_clean && !l.exclusion_reasons.some((r) => OFF_SCALE.has(r)))
					.map(toPoint),
				slope: state?.deg_rate_s_per_lap ?? null,
				intercept: state?.intercept_s ?? null,
				reliability: state?.fit_reliability ?? 'none',
				nFit: state?.n_laps_in_fit ?? 0
			};
		});
	});

	let all = $derived(series.flatMap((s) => [...s.clean, ...s.excluded]));
	let narrow = $derived(width < 480);
	let dims = $derived({
		W: width,
		H: narrow ? 236 : 270,
		L: narrow ? 50 : 58,
		R: narrow ? 88 : 104,
		T: 12,
		B: 34
	});
	let domain = $derived.by(() => {
		const ages = all.map((p) => p.age);
		const ys = all.map((p) => p.y);
		return {
			x0: 0,
			x1: Math.max(8, Math.max(0, ...ages) + 1),
			y0: Math.floor((Math.min(...ys) - 0.15) * 5) / 5,
			y1: Math.ceil((Math.max(...ys) + 0.15) * 5) / 5
		};
	});
	const x = (v: number) =>
		dims.L + ((v - domain.x0) / (domain.x1 - domain.x0)) * (dims.W - dims.L - dims.R);
	const y = (v: number) =>
		dims.T + (1 - (v - domain.y0) / (domain.y1 - domain.y0)) * (dims.H - dims.T - dims.B);
	let xStep = $derived([1, 2, 5, 10].find((s) => domain.x1 / s <= (narrow ? 5 : 9)) ?? 10);
	let xTicks = $derived(
		Array.from({ length: Math.floor(domain.x1 / xStep) + 1 }, (_, i) => i * xStep)
	);

	let fits = $derived.by(() => {
		const out: {
			s: Series;
			x1: number;
			y1: number;
			x2: number;
			y2: number;
			endY: number;
			text: string;
		}[] = [];
		for (const s of series) {
			if (s.slope === null || s.intercept === null || s.clean.length === 0) continue;
			const a0 = Math.min(...s.clean.map((p) => p.age));
			const a1 = Math.max(...s.clean.map((p) => p.age));
			const at = (age: number) => s.intercept! + s.slope! * age;
			out.push({
				s,
				x1: x(a0),
				y1: y(at(a0)),
				x2: x(a1),
				y2: y(at(a1)),
				endY: y(at(a1)),
				text: `${s.code} ${sgn(s.slope, 3)}${narrow ? '' : ' s/lap'}`
			});
		}
		if (out.length === 2 && Math.abs(out[0]!.endY - out[1]!.endY) < 16) {
			const [p, q] = out[0]!.endY <= out[1]!.endY ? [out[0]!, out[1]!] : [out[1]!, out[0]!];
			p.endY -= 8;
			q.endY += 8;
		}
		return out;
	});

	function tipFor(s: Series, p: Point, event: PointerEvent): void {
		showTip(
			`${s.code} · lap ${p.lap}`,
			[
				{ label: 'Fuel-corrected', value: fmtLap(p.y) },
				{ label: 'Tyre age', value: `${p.age} laps` },
				{ label: p.reason ? `Excluded: ${p.reason}` : 'Clean lap', value: '' }
			],
			event
		);
	}
</script>

<div class="chart-head">
	<div>
		<h3 class="chart-title">Tyre degradation, current stints</h3>
		<p class="chart-cap">
			Fuel-corrected lap time against tyre age, refit every lap on the laps you’ve seen. Hollow
			points are laps the clean-lap filter left out; hover for the reason. Dashed fits are not yet
			reliable. End labels are the slope in seconds per lap.
		</p>
	</div>
</div>
{#if series.length}
	<div class="legend" style="margin-bottom:8px">
		{#each series as s (s.driver)}
			<span
				><i class="key-line" class:dashed={s.reliability !== 'good'} style:border-color={s.color}
				></i>{s.code} · {compoundName(s.compound)}
				{#if s.reliability === 'none'}<span class="conf" data-level="1"
						><span>No fit yet · {s.clean.length} clean</span></span
					>{:else}<ConfBadge level={s.reliability} extra={`${s.nFit} clean`} />{/if}</span
			>
		{/each}
	</div>
{/if}
<div class="chart" use:measureWidth={(w) => (width = w)}>
	{#if all.length < 2}
		<div class="empty">
			No laps on these tyres yet. The curves appear after the first clean laps.
		</div>
	{:else}
		<svg
			viewBox="0 0 {dims.W} {dims.H}"
			role="img"
			aria-label={`Fuel-corrected lap time against tyre age for ${series.map((s) => s.code).join(' and ')}`}
		>
			{#each niceTicks(domain.y0, domain.y1, 5) as v (v)}
				<line x1={dims.L} x2={dims.W - dims.R} y1={y(v)} y2={y(v)} stroke="var(--grid)" />
				<text x={dims.L - 8} y={y(v) + 4} text-anchor="end">{fmtLap(v).slice(0, -2)}</text>
			{/each}
			{#each xTicks as v (v)}
				<text x={x(v)} y={dims.H - dims.B + 18} text-anchor="middle">{v}</text>
			{/each}
			<line
				x1={dims.L}
				x2={dims.W - dims.R}
				y1={dims.H - dims.B}
				y2={dims.H - dims.B}
				stroke="var(--axis)"
			/>
			<text
				x={(dims.L + dims.W - dims.R) / 2}
				y={dims.H - 2}
				text-anchor="middle"
				class="lbl"
				style="fill:var(--muted)">Tyre age (laps)</text
			>
			{#each fits as f (f.s.driver)}
				<line
					x1={f.x1}
					x2={f.x2}
					y1={f.y1}
					y2={f.y2}
					stroke={f.s.color}
					stroke-width="2"
					stroke-linecap="round"
					stroke-dasharray={f.s.reliability === 'good' ? undefined : '6 4'}
				/>
			{/each}
			{#each series as s (s.driver)}
				{#each s.excluded as p (p.lap)}
					{#if p.y >= domain.y0 && p.y <= domain.y1}
						<circle
							role="presentation"
							cx={x(p.age)}
							cy={y(p.y)}
							r="4"
							fill="var(--panel)"
							stroke={s.color}
							stroke-width="1.5"
							opacity="0.8"
							onpointerenter={(e) => tipFor(s, p, e)}
							onpointerleave={hideTip}
						/>
					{/if}
				{/each}
				{#each s.clean as p (p.lap)}
					{#if s.shape === 'circle'}
						<circle
							role="presentation"
							cx={x(p.age)}
							cy={y(p.y)}
							r="4.5"
							fill={s.color}
							stroke="var(--panel)"
							stroke-width="2"
							onpointerenter={(e) => tipFor(s, p, e)}
							onpointerleave={hideTip}
						/>
					{:else}
						<rect
							role="presentation"
							x={x(p.age) - 4.5}
							y={y(p.y) - 4.5}
							width="9"
							height="9"
							rx="1.5"
							fill={s.color}
							stroke="var(--panel)"
							stroke-width="2"
							onpointerenter={(e) => tipFor(s, p, e)}
							onpointerleave={hideTip}
						/>
					{/if}
				{/each}
			{/each}
			{#each fits as f (f.s.driver)}
				<line
					x1={f.x2 + 6}
					x2={dims.W - dims.R + 6}
					y1={f.endY}
					y2={f.endY}
					stroke="var(--rule-strong)"
				/>
				<text x={dims.W - dims.R + 10} y={f.endY + 4} class="lbl">{f.text}</text>
			{/each}
		</svg>
	{/if}
</div>
{#if series.length}
	<details class="data">
		<summary>Show as table</summary>
		<div class="table-scroll">
			<table class="data-t">
				<thead>
					<tr
						><th>Stint</th><th>Compound</th><th>Laps</th><th>Clean</th><th>Slope s/lap</th><th
							>Fit</th
						></tr
					>
				</thead>
				<tbody>
					{#each series as s (s.driver)}
						<tr>
							<td>{s.code} stint {s.stint ?? '—'}</td>
							<td>{compoundName(s.compound)}</td>
							<td class="num">{s.clean.length + s.excluded.length}</td>
							<td class="num">{s.clean.length}</td>
							<td class="num">{s.slope === null ? '—' : sgn(s.slope, 3)}</td>
							<td>{s.reliability}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
{/if}
