<!--
  "Our calls today" (ROADMAP W5): the race's settled calls, right against
  wrong, split by the confidence each carried, with what still waits and
  what is not counted. Only right and wrong count. The record on 2025 races
  the model never saw sits under it, for the kinds served.
-->
<script lang="ts">
	import type { Scorecard } from './ledger';
	import { HELD_OUT_RECORD, recordCell } from './record';
	import type { CallKind } from './view';

	interface Props {
		scorecard: Scorecard;
		kinds: readonly CallKind[];
		/** "right so far" while the race runs, "right" once it is over. */
		final?: boolean;
		compact?: boolean;
	}

	let { scorecard, kinds, final = false, compact = false }: Props = $props();

	let settled = $derived(scorecard.right + scorecard.wrong);
	const LABEL = { high: 'High', medium: 'Medium', low: 'Low' } as const;
	const KIND = { undercut: 'Undercut', rejoin: 'Rejoin' } as const;
	let notCounted = $derived(scorecard.void + scorecard.noCall);
	let records = $derived(kinds.map((k) => HELD_OUT_RECORD[k]));
</script>

<div class="score" class:compact data-testid="call-scorecard">
	{#if settled > 0}
		<p class="score-big">
			<span class="n">{scorecard.right}</span><span class="of">of {settled}</span><span class="word"
				>right{final ? '' : ' so far'}</span
			>
		</p>
	{:else}
		<p class="score-none">No call settled yet.</p>
	{/if}
	<div class="tiers">
		{#each scorecard.byConfidence as line (line.confidence)}
			{@const n = line.right + line.wrong}
			<div class="tier">
				<span class="tier-name">{LABEL[line.confidence]}</span>
				<div
					class="track"
					role="img"
					aria-label={n === 0
						? `${LABEL[line.confidence]} confidence: none settled yet`
						: `${LABEL[line.confidence]} confidence: ${line.right} right, ${line.wrong} wrong`}
				>
					{#if n > 0}
						<div class="seg-r" style:width="{(line.right / n) * 100}%"></div>
						<div class="seg-w" style:width="{(line.wrong / n) * 100}%"></div>
					{/if}
				</div>
				<span class="tier-n mono" class:muted={n === 0}
					>{n === 0 ? 'none' : `${line.right} / ${n}`}</span
				>
			</div>
		{/each}
	</div>
	{#if scorecard.pending > 0 || notCounted > 0}
		<p class="score-foot">
			{[
				scorecard.pending > 0 ? `${scorecard.pending} waiting on how the stops play out` : null,
				notCounted > 0 ? `${notCounted} not counted (not a fair test, or too little to call)` : null
			]
				.filter(Boolean)
				.join(' · ')}
		</p>
	{/if}
	{#if records.length > 0}
		<div class="record">
			<div class="caps">
				On {records[0]?.races} races of {records[0]?.season} the model never saw
			</div>
			<table>
				<thead>
					<tr
						><th scope="col">Call</th><th scope="col">High</th><th scope="col">Medium</th><th
							scope="col">Low</th
						></tr
					>
				</thead>
				<tbody>
					{#each records as record (record.kind)}
						<tr>
							<th scope="row">{KIND[record.kind]}</th>
							{#each record.lines as line (line.confidence)}
								<td class="mono">{recordCell(line)}</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
			{#if records.some((r) => !r.passed)}
				<p class="score-foot" data-testid="record-not-passed">
					Not cleared yet: on those races high, medium and low calls did not rank in that order, so
					a kind not cleared is shown here for development only.
				</p>
			{/if}
		</div>
	{/if}
</div>

<style>
	.score-big {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin: 8px 0 0;
	}
	.n {
		font: 500 52px/1 var(--font-display);
		color: var(--ink);
	}
	.of {
		font: 500 26px/1 var(--font-display);
		color: var(--muted);
	}
	.word {
		font-size: 16px;
		color: var(--ink-2);
	}
	.compact .n {
		font-size: 36px;
	}
	.compact .of {
		font-size: 18px;
	}
	.score-none {
		margin: 8px 0 0;
		font: 500 18px/1.3 var(--font-display);
		color: var(--ink-2);
	}
	.tiers {
		display: grid;
		gap: 8px;
		margin-top: 14px;
	}
	.tier {
		display: grid;
		grid-template-columns: 64px 1fr 56px;
		gap: 10px;
		align-items: center;
		font-size: 13px;
	}
	.tier-name {
		color: var(--ink-2);
	}
	.track {
		height: 10px;
		border-radius: 5px;
		background: var(--panel-sunk);
		display: flex;
		overflow: hidden;
	}
	.seg-r {
		background: var(--pb);
	}
	.seg-w {
		background: var(--flag-red);
	}
	.tier-n {
		text-align: right;
	}
	.muted {
		color: var(--muted);
	}
	.score-foot {
		margin: 12px 0 0;
		font-size: 12px;
		color: var(--muted);
	}
	.record {
		margin-top: 12px;
		padding-top: 12px;
		border-top: 1px solid var(--rule);
	}
	.record table {
		width: 100%;
		margin-top: 8px;
		border-collapse: collapse;
		font-size: 12.5px;
	}
	.record th,
	.record td {
		padding: 3px 4px 3px 0;
		text-align: left;
		font-weight: 400;
	}
	.record thead th {
		color: var(--muted);
		font-size: 11.5px;
	}
	.record td {
		color: var(--accent);
	}
	.mono {
		font-family: var(--font-mono);
		font-variant-numeric: tabular-nums;
	}
	.caps {
		font: 500 11px/1.2 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
</style>
