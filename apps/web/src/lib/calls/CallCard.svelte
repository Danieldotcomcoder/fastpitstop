<!--
  One call (ROADMAP W5 mockup "A call card, in each state"): the same card
  on the desk, the phone and the ledger page. The words say what was called
  and what happened; the mark and the word beside it say how it settled, so
  colour is never the only signal.
-->
<script lang="ts">
	import ConfBadge from '$lib/desk/ConfBadge.svelte';
	import { colorForTeam } from '$lib/teams';
	import { callText } from './text';
	import type { CallOut } from './view';

	interface Props {
		call: CallOut;
		name: (driver: string) => string;
		team: (driver: string) => string | null;
	}

	let { call, name, team }: Props = $props();

	let text = $derived(callText(call, name));
</script>

<article class="call-card" data-status={call.status} data-testid="call-card">
	<div class="call-head">
		<span class="call-lap"
			><span class="lap mono">{text.lap}</span> <span class="kind">{text.kind}</span></span
		>
		<span class="call-tags">
			<ConfBadge level={call.confidence} />
			<span class="outcome" data-status={call.status}>
				{#if call.status === 'right'}
					<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"
						><path
							d="M2 7.5l3 3 7-7"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						/></svg
					>
				{:else if call.status === 'wrong'}
					<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"
						><path
							d="M3 3l8 8M11 3l-8 8"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
						/></svg
					>
				{:else if call.status === 'pending'}
					<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"
						><circle
							cx="7"
							cy="7"
							r="5.5"
							fill="none"
							stroke="currentColor"
							stroke-width="1.5"
						/><path
							d="M7 4v3.2l2 1.3"
							fill="none"
							stroke="currentColor"
							stroke-width="1.5"
							stroke-linecap="round"
						/></svg
					>
				{:else if call.status === 'void'}
					<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"
						><circle
							cx="7"
							cy="7"
							r="5.5"
							fill="none"
							stroke="currentColor"
							stroke-width="1.5"
						/><path d="M3.2 10.8l7.6-7.6" stroke="currentColor" stroke-width="1.5" /></svg
					>
				{/if}
				{text.outcome}
			</span>
		</span>
	</div>
	<p class="call-body">
		<i class="team" style:background={colorForTeam(team(call.driver))} aria-hidden="true"
		></i>{text.body}
	</p>
	{#if text.note}<p class="call-note">{text.note}</p>{/if}
</article>

<style>
	.call-card {
		padding: 12px 0;
		border-top: 1px solid var(--rule);
	}
	.call-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.call-lap {
		display: inline-flex;
		align-items: baseline;
		gap: 8px;
	}
	.lap {
		font: 500 13px/1 var(--font-mono);
		color: var(--accent);
	}
	.kind {
		font: 500 10.5px/1 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.call-tags {
		display: inline-flex;
		align-items: center;
		gap: 10px;
	}
	.outcome {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		font: 600 13px/1 var(--font-ui);
		color: var(--muted);
	}
	.outcome[data-status='right'] {
		color: var(--pb);
	}
	.outcome[data-status='wrong'] {
		color: var(--flag-red);
	}
	.call-body {
		margin: 6px 0 0;
		font-size: 14px;
		line-height: 1.45;
		color: var(--ink);
	}
	.team {
		display: inline-block;
		width: 3px;
		height: 13px;
		margin-right: 7px;
		vertical-align: -2px;
		border-radius: 1px;
	}
	.call-note {
		margin: 4px 0 0;
		font-size: 12.5px;
		color: var(--muted);
	}
	.mono {
		font-variant-numeric: tabular-nums;
	}
</style>
