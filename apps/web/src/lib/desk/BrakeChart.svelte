<!--
  Braking-point drift: how far each corner's brake point moves per lap of
  tyre age, per corner, with its 95% interval. Positive means braking
  earlier as the tyres fade.

  These are whole-stint fits, so a stint's line only appears once that
  stint is provably over on the viewer's clock (`isStintRevealed`): showing
  it earlier would use laps the viewer has not seen yet. Hence "latest
  completed stint", not the design's "current stint".
-->
<script lang="ts">
	import { api, type components } from '$lib/api/client';
	import { measureWidth, niceTicks, pairColors } from './charts';
	import type { FocusPair } from './focus';
	import { sgn, surname } from './format';
	import { hideTip, showTip } from './tooltip';

	type Drift = components['schemas']['BrakingDriftOut'];

	interface Props {
		apiKey: string;
		pair: FocusPair | null;
		codes: Record<string, string>;
		names: Record<string, string>;
		teams: Record<string, string | null>;
		isStintRevealed: (driver: string, stint: number) => boolean;
		/** Bumped whenever the released log grows, so reveals re-derive. */
		revision: number;
	}

	let { apiKey, pair, codes, names, teams, isStintRevealed, revision }: Props = $props();

	let side = $state<'a' | 'b'>('a');
	let status = $state<'idle' | 'loading' | 'ready' | 'error'>('idle');
	let drift = $state<Drift[]>([]);
	let width = $state(640);
	let version = 0;

	// One analysis request per pair: the kernel's braking run is the
	// expensive part, and the pair changes only when the viewer refocuses.
	let pairKey = $derived(pair ? [pair.a, pair.b].sort().join(',') : '');
	$effect(() => {
		const key = pairKey;
		const session = apiKey;
		if (!key) return;
		const own = ++version;
		status = 'loading';
		api
			.GET('/sessions/{key}/analysis', {
				params: { path: { key: session }, query: { braking_drivers: key.split(',') } }
			})
			.then(({ data, error }) => {
				if (own !== version) return;
				drift = error || !data ? [] : (data.braking_drift ?? []);
				status = error ? 'error' : 'ready';
			})
			.catch(() => {
				if (own === version) status = 'error';
			});
	});

	let who = $derived(pair ? (side === 'b' ? pair.b : pair.a) : null);
	let colors = $derived(
		pair ? pairColors(teams[pair.a] ?? null, teams[pair.b] ?? null) : { a: '', b: '' }
	);
	let color = $derived(side === 'b' ? colors.b : colors.a);

	/** The latest stint of `who` whose fit the viewer is allowed to see. */
	let rows = $derived.by(() => {
		void revision;
		if (!who) return [];
		const own = drift.filter((d) => d.driver === who && isStintRevealed(d.driver, d.stint_number));
		const latest = Math.max(-1, ...own.map((d) => d.stint_number));
		return own
			.filter((d) => d.stint_number === latest)
			.sort((x, y) => x.corner_number - y.corner_number);
	});
	let stint = $derived(rows[0]?.stint_number ?? null);
	let hasAny = $derived(drift.some((d) => d.driver === who));

	let dims = $derived.by(() => {
		const rowH = 34;
		const W = width;
		const R = W < 480 ? 96 : 150;
		return { W, rowH, L: 46, R, T: 26, B: 30, H: 26 + rowH * Math.max(1, rows.length) + 30 };
	});
	let domain = $derived.by(() => {
		const values = rows.flatMap((r) => [r.drift_ci_low_m_per_lap, r.drift_ci_high_m_per_lap]);
		return {
			lo: Math.min(-0.5, Math.floor(Math.min(0, ...values) * 2) / 2),
			hi: Math.max(1, Math.ceil(Math.max(0, ...values) * 2) / 2)
		};
	});
	const x = (v: number) =>
		dims.L + ((v - domain.lo) / (domain.hi - domain.lo)) * (dims.W - dims.L - dims.R);
	const rowY = (index: number) => dims.T + dims.rowH * index + dims.rowH / 2;
	const cornerLabel = (d: Drift) => `T${d.corner_number}${d.corner_letter ?? ''}`;

	function tipFor(d: Drift, event: PointerEvent): void {
		showTip(
			cornerLabel(d),
			[
				{ label: 'Drift', value: `${sgn(d.drift_m_per_lap)} m/lap`, color },
				{
					label: '95% interval',
					value: `${sgn(d.drift_ci_low_m_per_lap)} to ${sgn(d.drift_ci_high_m_per_lap)}`
				},
				{ label: 'Fit', value: `${d.reliability}, ${d.n_laps_used} laps` }
			],
			event
		);
	}
</script>

<div class="chart-head">
	<div>
		<h3 class="chart-title">
			Braking-point drift{#if who}, {surname(names[who] ?? null) ??
					codes[who] ??
					who}{/if}{#if stint !== null}, stint {stint}{/if}
		</h3>
		<p class="chart-cap">
			How far the brake point moves per lap of tyre age, per corner. Positive means braking earlier
			as the tyres fade. Lines are 95% intervals; hollow dots are fits that are not yet reliable. A
			stint appears once it has finished on your clock.
		</p>
	</div>
	{#if pair}
		<div class="seg" role="group" aria-label="Driver">
			<button type="button" aria-pressed={side === 'a'} onclick={() => (side = 'a')}
				>{codes[pair.a] ?? pair.a}</button
			>
			<button type="button" aria-pressed={side === 'b'} onclick={() => (side = 'b')}
				>{codes[pair.b] ?? pair.b}</button
			>
		</div>
	{/if}
</div>
<div class="chart" use:measureWidth={(w) => (width = w)}>
	{#if status === 'loading' || status === 'idle'}
		<div class="empty">Reading the braking traces…</div>
	{:else if status === 'error'}
		<div class="empty">Braking analysis is unavailable right now.</div>
	{:else if !hasAny}
		<div class="empty">
			<h4 class="chart-title">No corner-level braking data yet.</h4>
			This session’s feed carries no brake telemetry for {who
				? (codes[who] ?? who)
				: 'this driver'}, so there is nothing to measure.
		</div>
	{:else if rows.length === 0}
		<div class="empty">The first stint’s braking trend appears once it finishes on your clock.</div>
	{:else}
		<svg viewBox="0 0 {dims.W} {dims.H}" role="img" aria-label="Braking point drift by corner">
			{#each niceTicks(domain.lo, domain.hi, 6) as v (v)}
				<line
					x1={x(v)}
					x2={x(v)}
					y1={dims.T - 6}
					y2={dims.H - dims.B}
					stroke={v === 0 ? 'var(--axis)' : 'var(--grid)'}
				/>
				<text x={x(v)} y={dims.H - dims.B + 16} text-anchor="middle"
					>{v > 0 ? '+' : v < 0 ? '−' : ''}{Math.abs(v)}</text
				>
			{/each}
			<text
				x={x(0) - 6}
				y={dims.T - 12}
				text-anchor="end"
				class="lbl"
				style="fill:var(--muted);font-size:10.5px">← later</text
			>
			<text x={x(0) + 6} y={dims.T - 12} class="lbl" style="fill:var(--muted);font-size:10.5px"
				>earlier →</text
			>
			<text
				x={(dims.L + dims.W - dims.R) / 2}
				y={dims.H - 2}
				text-anchor="middle"
				class="lbl"
				style="fill:var(--muted)">Brake point drift (m per lap of tyre age)</text
			>
			{#each rows as d, index (d.corner_number + (d.corner_letter ?? ''))}
				{@const cy = rowY(index)}
				{@const good = d.reliability === 'good'}
				<text x={dims.L - 10} y={cy + 4} text-anchor="end" class="lbl">{cornerLabel(d)}</text>
				<line
					x1={x(d.drift_ci_low_m_per_lap)}
					x2={x(d.drift_ci_high_m_per_lap)}
					y1={cy}
					y2={cy}
					stroke={color}
					stroke-width="2"
					stroke-linecap="round"
					opacity="0.55"
				/>
				<circle
					role="presentation"
					cx={x(d.drift_m_per_lap)}
					{cy}
					r="5"
					fill={good ? color : 'var(--panel)'}
					stroke={good ? 'var(--panel)' : color}
					stroke-width="2"
					onpointerenter={(e) => tipFor(d, e)}
					onpointerleave={hideTip}
				/>
				<text x={dims.W - dims.R + 14} y={cy + 4} class="lbl">{sgn(d.drift_m_per_lap)} m/lap</text>
			{/each}
		</svg>
	{/if}
</div>
{#if rows.length}
	<details class="data">
		<summary>Show as table</summary>
		<div class="table-scroll">
			<table class="data-t">
				<thead>
					<tr><th>Corner</th><th>Drift m/lap</th><th>95% interval</th><th>Laps</th><th>Fit</th></tr>
				</thead>
				<tbody>
					{#each rows as d (d.corner_number + (d.corner_letter ?? ''))}
						<tr>
							<td>{cornerLabel(d)}</td>
							<td class="num">{sgn(d.drift_m_per_lap)}</td>
							<td class="num"
								>{sgn(d.drift_ci_low_m_per_lap)} to {sgn(d.drift_ci_high_m_per_lap)}</td
							>
							<td class="num">{d.n_laps_used}</td>
							<td>{d.reliability}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
{/if}
