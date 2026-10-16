<!--
  The desk's calls (ROADMAP W5 mockups "Race desk" and "Phone · Strategy"):
  today's score and the latest calls, each settled as right, wrong or not
  counted. On a phone this is the Strategy tab's top, with where the focused
  car comes out if it boxes now above it; on a wider screen that strip is
  on the pit board instead.

  Mounted only when the stream serves at least one call kind, which in
  production means a kind that passed its held-out backtest.
-->
<script lang="ts">
	import CallCard from '$lib/calls/CallCard.svelte';
	import RejoinStrip from '$lib/calls/RejoinStrip.svelte';
	import Scorecard from '$lib/calls/Scorecard.svelte';
	import {
		latestFirst,
		type CallKind,
		type CallOut,
		type Scorecard as ScorecardModel,
		type StripModel
	} from '$lib/calls';

	interface Props {
		calls: readonly CallOut[];
		scorecard: ScorecardModel;
		kinds: readonly CallKind[];
		strip: StripModel | null;
		final: boolean;
		/** The ledger page for this session, held to the viewer's lap. */
		ledgerHref: string;
		name: (driver: string) => string;
		team: (driver: string) => string | null;
	}

	let { calls, scorecard, kinds, strip, final, ledgerHref, name, team }: Props = $props();

	const LATEST = 3;
	let latest = $derived(latestFirst(calls).slice(0, LATEST));
</script>

<section class="p-calls" aria-label="Our calls" data-testid="calls-panel">
	{#if strip}
		<div class="panel calls-rejoin">
			<RejoinStrip model={strip} />
		</div>
	{/if}
	<div class="panel">
		<div class="calls-head">
			<h2 class="caps" id="callsTitle">Our calls today</h2>
			{#if calls.length > 0}
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
				<a href={ledgerHref} data-testid="calls-ledger-link"
					>All {calls.length} call{calls.length === 1 ? '' : 's'}</a
				>
			{/if}
		</div>
		<Scorecard {scorecard} {kinds} {final} />
	</div>
	<div class="panel">
		<h2 class="caps">Latest calls</h2>
		{#if latest.length === 0}
			<p class="calls-none">
				No car has boxed with a call on it yet. A call is the one on screen the lap before the car
				boxes.
			</p>
		{:else}
			<ol class="calls-list">
				{#each latest as call (call.call_id)}
					<li><CallCard {call} {name} {team} /></li>
				{/each}
			</ol>
		{/if}
	</div>
</section>

<style>
	.p-calls {
		display: grid;
		gap: var(--gap);
		align-content: start;
	}
	.calls-head {
		display: flex;
		justify-content: space-between;
		align-items: baseline;
		gap: 12px;
	}
	.calls-head h2,
	.p-calls h2 {
		margin: 0;
	}
	.calls-head a {
		font-size: 13px;
	}
	.calls-rejoin {
		background: var(--board);
		border-color: var(--board-edge);
		color: var(--board-ink);
		box-shadow: none;
	}
	.calls-rejoin :global(.caps) {
		color: var(--warn);
	}
	.calls-list {
		list-style: none;
		margin: 8px 0 0;
		padding: 0;
	}
	.calls-none {
		margin: 8px 0 0;
		font-size: 13px;
		color: var(--muted);
	}
	/* The strip lives on the pit board where there is room for both. */
	@media (min-width: 900px) {
		.calls-rejoin {
			display: none;
		}
	}
</style>
