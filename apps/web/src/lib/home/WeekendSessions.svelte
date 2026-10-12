<!--
  A weekend's sessions: each at the track and in the viewer's own time, the
  next one highlighted, those already started dimmed. Shared by the home
  page's next-race block and the Grand Prix pages. The viewer's time is
  only known in the browser, so the server leaves that column blank.
-->
<script lang="ts">
	import { sessionTime } from '$lib/ui';
	import { sessionPhase, trackTime, type ScheduleEvent } from './next-race';

	interface Props {
		event: ScheduleEvent;
		nowMs: number;
		inBrowser: boolean;
	}

	let { event, nowMs, inBrowser }: Props = $props();
</script>

<ol class="sessions" aria-label="Sessions this weekend">
	{#each event.sessions as session (session.start_utc)}
		{@const phase = sessionPhase(session, event, nowMs)}
		<li class={phase}>
			<span class="name">{session.name}</span>
			<span class="at num">{trackTime(session)} <span class="muted">at the track</span></span>
			<span class="yours num"
				>{#if inBrowser}{sessionTime(session.start_utc)}
					<span class="muted">your time</span>{:else}&nbsp;{/if}</span
			>
		</li>
	{/each}
</ol>

<style>
	.sessions {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
	}
	.sessions li {
		display: grid;
		grid-template-columns: minmax(8.5rem, 1fr) auto;
		gap: 2px var(--space-4);
		padding: var(--space-2) 0;
		border-top: 1px solid var(--rule);
	}
	@media (min-width: 640px) {
		.sessions li {
			grid-template-columns: minmax(9rem, 1fr) auto minmax(11rem, auto);
		}
	}
	.sessions li.done {
		color: var(--muted);
	}
	.sessions li.next .name {
		color: var(--accent);
		font-weight: 600;
	}
	.at,
	.yours {
		font-family: var(--font-mono);
		font-size: var(--font-size-sm);
		text-align: right;
		white-space: nowrap;
	}
	.yours {
		grid-column: 2;
	}
	@media (min-width: 640px) {
		.yours {
			grid-column: 3;
		}
	}
	.muted {
		color: var(--muted);
		font-family: var(--font-ui);
		font-size: var(--font-size-xs);
	}
</style>
