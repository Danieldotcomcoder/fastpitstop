<!--
  The qualifying board (ROADMAP W4, mockup M2-Quali): who goes through from
  this part, with the cut line; what each car in the drop zone needs; every
  time against the cut; who is on track now; and the knockouts so far.

  Draws `qualiView` (pure, tested) and nothing else: no number here is
  computed in the component. Spoiler-safe because the view is folded from
  the delay-synced log only.
-->
<script lang="ts">
	import { fmtLap } from '$lib/desk/format';
	import { colorForTeam } from '$lib/teams';
	import { dropZoneSentences, needsToBeat, type QualiRow, type QualiView } from './reducer';

	interface Props {
		view: QualiView;
		/** Display name per driver number. */
		names: Record<string, string>;
	}

	let { view, names }: Props = $props();

	/** Family names, as a commentator says them: "Ocon", "Hamilton". */
	const name = (row: QualiRow) => names[row.driver]?.split(' ').at(-1) ?? row.code;
	const STATUS = { pit: 'In pits', out: 'Out lap', track: 'On track' } as const;

	let nextPart = $derived(
		view.part !== null && view.part < 3 ? `${view.label?.slice(0, -1)}${view.part + 1}` : null
	);
	let title = $derived(
		view.part === null ? 'Qualifying' : nextPart ? `Who makes ${nextPart}` : 'The fight for pole'
	);
	let subtitle = $derived(
		view.part === null
			? 'Waiting for the first part to start.'
			: nextPart
				? `Best ${view.label} lap so far · the top ${view.advance} go through`
				: `Best ${view.label} lap so far · quickest takes pole`
	);
	let sentences = $derived(dropZoneSentences(view, name, fmtLap));
	let onTrack = $derived(view.rows.filter((r) => r.status === 'out' || r.status === 'track'));
	let needs = $derived(view.cutTime !== null ? needsToBeat(view.cutTime) : null);

	// The strip: every time against the cut, quicker to the left.
	const STRIP = { w: 560, lo: -0.8, hi: 0.5 } as const;
	const sx = (d: number) =>
		20 +
		((Math.min(Math.max(d, STRIP.lo), STRIP.hi) - STRIP.lo) / (STRIP.hi - STRIP.lo)) *
			(STRIP.w - 40);
	let marks = $derived(
		view.cutTime === null
			? []
			: view.rows
					.filter((r) => r.best !== null)
					.map((r) => ({ row: r, d: r.best! - view.cutTime!, onLap: r.status === 'track' }))
	);
</script>

<section class="panel q-board" aria-labelledby="q-title" data-testid="qualifying-board">
	<div class="panel-head">
		<div>
			<h2 class="panel-title" id="q-title">{title}</h2>
			<p class="panel-sub">{subtitle}</p>
		</div>
	</div>

	{#if view.rows.length}
		<ol class="q-rows" aria-label="Order in {view.label ?? 'qualifying'}">
			{#each view.rows as row (row.driver)}
				<li class:danger={row.inDanger} class:improved={row.improved} data-code={row.code}>
					<span class="pos num">{row.position ?? '—'}</span>
					<span class="who"
						><i style:background={colorForTeam(row.team)}></i><b class="num">{row.code}</b>
						<span class="team">{row.team ?? ''}</span></span
					>
					<span class="best num">{fmtLap(row.best)}</span>
					<span class="gap num"
						>{row.gap !== null && row.gap > 0 ? `+${row.gap.toFixed(3)}` : ''}</span
					>
					<span class="status s-{row.status ?? 'none'}">{row.status ? STATUS[row.status] : ''}</span
					>
					<span class="need"
						>{#if row.inDanger}<span class="chip num"
								>{needs !== null ? `needs ${fmtLap(needs)}` : 'needs a lap'}</span
							>{/if}</span
					>
				</li>
				{#if view.advance !== null && row === view.rows[view.advance - 1] && view.rows.length > view.advance}
					<li class="cut" aria-label="The cut">
						<span></span><b class="num"
							>{nextPart} CUT{view.cutTime !== null ? ` · ${fmtLap(view.cutTime)}` : ''}</b
						><span></span>
					</li>
				{/if}
			{/each}
		</ol>
	{:else}
		<p class="panel-sub">No car has reached the timing feed yet.</p>
	{/if}

	{#each view.knockouts as group (group.part)}
		<div class="q-out">
			<h3 class="caps">Out in {view.label?.startsWith('SQ') ? 'SQ' : 'Q'}{group.part}</h3>
			<ol aria-label="Knocked out in part {group.part}">
				{#each group.rows as r (r.driver)}
					<li class="num"><span>{r.position}</span> {r.code} {fmtLap(r.best)}</li>
				{/each}
			</ol>
		</div>
	{/each}
</section>

<div class="q-side">
	{#if sentences.length}
		<section class="panel q-drop" aria-labelledby="q-drop-title">
			<h2 class="caps warn" id="q-drop-title">The drop zone</h2>
			<p class="lead">{sentences[0]}</p>
			{#if sentences[1]}<p class="panel-sub">{sentences[1]}</p>{/if}
			{#if marks.length}
				<svg
					viewBox="0 0 {STRIP.w} 70"
					role="img"
					aria-label="Every {view.label} time against the cut"
				>
					<rect class="zone" x={sx(0)} y="18" width={sx(STRIP.hi) - sx(0)} height="32"></rect>
					<line class="axis" x1="20" y1="34" x2={STRIP.w - 20} y2="34"></line>
					<line class="cutline" x1={sx(0)} y1="12" x2={sx(0)} y2="54"></line>
					{#each marks as m (m.row.driver)}
						<circle
							cx={sx(m.d)}
							cy="34"
							r={m.onLap ? 6 : 5}
							fill={colorForTeam(m.row.team)}
							class:beyond={m.d > STRIP.hi}
						></circle>
					{/each}
					{#each [-0.8, -0.4, 0, 0.4] as v (v)}
						<text x={sx(v)} y="64" text-anchor="middle">{v > 0 ? '+' : ''}{v.toFixed(1)}</text>
					{/each}
				</svg>
				<p class="strip-key">
					<span>quicker than the cut</span><span>slower: out, unless they improve</span>
				</p>
			{/if}
		</section>
	{/if}

	<section class="panel q-track" aria-labelledby="q-track-title">
		<h2 class="caps" id="q-track-title">On track now</h2>
		{#if onTrack.length}
			<ul>
				{#each onTrack as row (row.driver)}
					<li>
						<span class="who"
							><i style:background={colorForTeam(row.team)}></i><b class="num">{row.code}</b>
							<span class="muted">{row.position !== null ? `P${row.position}` : ''}</span></span
						>
						<span class="num" class:warn={row.inDanger}
							>{row.status === 'out' ? 'Out lap · ' : ''}{row.inDanger
								? needs !== null
									? `must beat ${fmtLap(needs)}`
									: 'needs a lap'
								: row.position === null
									? 'no time yet'
									: view.advance !== null
										? 'through for now'
										: row.position === 1
											? 'provisional pole'
											: `P${row.position} for now`}</span
						>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="panel-sub">Every car is in the pits.</p>
		{/if}
		{#if view.rows[0]?.best != null}
			<p class="panel-foot">
				Quickest in {view.label}: {name(view.rows[0])}, {fmtLap(view.rows[0].best)}.
			</p>
		{/if}
	</section>
</div>

<style>
	.q-rows,
	.q-out ol,
	.q-track ul {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	.q-rows li {
		display: grid;
		grid-template-columns: 1.6rem minmax(0, 1fr) 5.2rem 4.4rem 5.6rem minmax(0, 7.5rem);
		gap: 8px;
		align-items: center;
		padding: 7px 4px;
		border-top: 1px solid var(--rule);
		font-size: 13px;
	}

	.q-rows li.improved .best {
		color: var(--good);
	}

	.pos {
		color: var(--muted);
	}

	.who {
		display: flex;
		align-items: baseline;
		gap: 6px;
		min-width: 0;
		white-space: nowrap;
		overflow: hidden;
	}

	.who i {
		flex: none;
		width: 4px;
		height: 15px;
		border-radius: 2px;
		align-self: center;
	}

	.team {
		color: var(--muted);
		font-size: 12px;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.best,
	.gap {
		text-align: right;
	}

	.gap {
		color: var(--ink-2);
		font-size: 12px;
	}

	.status {
		font-size: 12px;
		color: var(--muted);
	}

	.status.s-track {
		color: var(--accent);
	}

	.status.s-out {
		color: var(--ink-2);
	}

	.need {
		text-align: right;
	}

	.chip {
		display: inline-block;
		font-size: 11px;
		font-weight: 600;
		color: var(--warn);
		border: 1px solid var(--warn);
		border-radius: 999px;
		padding: 2px 7px;
		white-space: nowrap;
	}

	.q-rows li.cut {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 6px 0;
		border-top: 0;
	}

	.q-rows li.cut span {
		flex: 1;
		height: 2px;
		background: var(--warn);
	}

	.q-rows li.cut b {
		font-size: 11px;
		letter-spacing: 0.08em;
		color: var(--warn);
	}

	.q-out {
		margin-top: 12px;
		padding-top: 10px;
		border-top: 1px solid var(--rule);
	}

	.q-out h3 {
		margin: 0 0 6px;
	}

	.q-out ol {
		display: flex;
		flex-wrap: wrap;
		gap: 6px 18px;
		color: var(--muted);
		font-size: 12.5px;
	}

	.q-side {
		display: flex;
		flex-direction: column;
		gap: var(--gap);
		min-width: 0;
	}

	.warn {
		color: var(--warn);
	}

	.lead {
		margin: 8px 0 0;
		font: 500 19px/1.3 var(--f-display);
	}

	.q-drop svg {
		display: block;
		width: 100%;
		height: auto;
		margin-top: 10px;
	}

	.zone {
		fill: var(--warn);
		opacity: 0.12;
	}

	.axis {
		stroke: var(--rule);
	}

	.cutline {
		stroke: var(--warn);
		stroke-width: 2;
	}

	circle {
		stroke: var(--panel);
		stroke-width: 1.5;
	}

	circle.beyond {
		opacity: 0.6;
	}

	svg text {
		font: 500 10px var(--f-mono);
		fill: var(--muted);
	}

	.strip-key {
		display: flex;
		justify-content: space-between;
		margin: 0;
		font-size: 11px;
		color: var(--muted);
	}

	.q-track li {
		display: flex;
		justify-content: space-between;
		gap: 10px;
		padding: 7px 0;
		border-top: 1px solid var(--rule);
		font-size: 13px;
	}

	.muted {
		color: var(--muted);
	}

	/* A phone: the order, the time and what a car needs. */
	@media (max-width: 640px) {
		.q-rows li {
			grid-template-columns: 1.4rem minmax(0, 1fr) 5rem auto;
		}
		.team,
		.gap,
		.status {
			display: none;
		}
	}
</style>
