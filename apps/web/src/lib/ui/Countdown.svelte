<!--
  Time left until a session starts, ticking each second, plus the start in
  the viewer's own time zone. Runs on the server-corrected clock, so a
  phone whose clock is off still counts down to the right moment.
-->
<script lang="ts">
	import { serverNowMs } from '$lib/delay-sync';
	import { countdownParts, formatCountdown, sessionTime } from './format';

	interface Props {
		/** The session start, UTC ISO 8601. */
		startUtc: string;
		label?: string;
		/** Shown once the start has passed. */
		pastText?: string;
		/** Injected for tests and the design gallery. */
		now?: () => number;
	}

	let {
		startUtc,
		label = 'Lights out in',
		pastText = 'Started',
		now = serverNowMs
	}: Props = $props();

	let nowMs = $state(serverNowMs());
	$effect(() => {
		nowMs = now();
		const timer = setInterval(() => (nowMs = now()), 1000);
		return () => clearInterval(timer);
	});

	let target = $derived(new Date(startUtc).getTime());
	let parts = $derived(countdownParts(target, nowMs));
	let local = $derived(sessionTime(startUtc));
</script>

<div class="countdown">
	<span class="label">{label}</span>
	<span class="time num" role="timer" aria-live="off">
		{parts.past ? pastText : formatCountdown(parts)}
	</span>
	<span class="local">{local} your time</span>
</div>

<style>
	.countdown {
		display: inline-flex;
		flex-direction: column;
		gap: 2px;
	}
	.label {
		font: 500 var(--font-size-xs) / 1.3 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted);
	}
	.time {
		font: 500 var(--num-lg) / 1.1 var(--font-mono);
		font-variant-numeric: tabular-nums;
		color: var(--ink);
	}
	.local {
		font-size: var(--font-size-sm);
		color: var(--ink-2);
	}
</style>
