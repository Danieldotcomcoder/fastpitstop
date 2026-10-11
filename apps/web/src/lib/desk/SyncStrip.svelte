<!--
  The spoiler shield: the design's sticky Delay Sync strip. One control for
  the one clock everything on the page is gated by — the delay slider,
  tap-to-sync at lights out, the track status, and (replays only) speed and
  restart. It is also the full-width flag banner (W4): tinted for a yellow,
  safety car, VSC or red flag, from the released track status only.
-->
<script lang="ts">
	import {
		playbackClockMs,
		serverNowMs,
		type DelaySyncController,
		type DelayState
	} from '$lib/delay-sync';
	import type { TrackStatusChip } from './race-feed';

	interface Props {
		controller: DelaySyncController;
		isLive: boolean;
		speed: number;
		status: TrackStatusChip;
		/** The instant lights went out (event time), for tap-to-sync. */
		lightsOutUtc: string | null;
		onSpeed: (speed: number) => void;
		onRestart: () => void;
	}

	let { controller, isLive, speed, status, lightsOutUtc, onSpeed, onRestart }: Props = $props();

	const SPEEDS = [1, 4, 16] as const;

	let delay = $state<DelayState>({ delaySeconds: 0, synced: false });
	$effect(() => controller.delayState.subscribe((value) => (delay = value)));

	let fromTap = $state(false);
	let toast = $state('');
	let toastTimer: ReturnType<typeof setTimeout> | null = null;

	let clock = $state('');
	$effect(() => {
		const tick = () => {
			const ms = playbackClockMs(serverNowMs(), controller.delayState.get().delaySeconds);
			clock = new Date(ms).toLocaleTimeString([], { hour12: false });
		};
		tick();
		const handle = setInterval(tick, 500);
		return () => clearInterval(handle);
	});

	function onSlide(event: Event): void {
		fromTap = false;
		controller.setDelaySeconds(Number((event.currentTarget as HTMLInputElement).value));
	}

	function onTap(): void {
		if (lightsOutUtc === null) return;
		// The server's clock, like the lights-out timestamp it is compared with.
		const seconds = controller.calibrateFromTap(serverNowMs(), lightsOutUtc);
		fromTap = true;
		toast = `Synced: ${seconds.toFixed(1)} s behind live`;
		if (toastTimer) clearTimeout(toastTimer);
		toastTimer = setTimeout(() => (toast = ''), 4000);
	}
</script>

<section class="sync flag-{status.tone}" aria-label="Delay Sync">
	<div class="shield">
		<span class="shield-icon"><svg width="16" height="16"><use href="#i-shield" /></svg></span>
		<div class="shield-text">
			<strong>{fromTap ? 'Synced from your tap' : 'Spoiler shield on'}</strong>
			<span
				><span class="num" data-testid="shield-delay">{delay.delaySeconds.toFixed(1)} s</span>
				behind live<span class="long">&nbsp;· nothing past your clock is shown</span></span
			>
		</div>
	</div>
	<div class="delay-ctl">
		<label for="delay" class="caps">Delay</label>
		<input
			type="range"
			id="delay"
			min={controller.minDelaySeconds}
			max={controller.maxDelaySeconds}
			step="0.5"
			value={delay.delaySeconds}
			oninput={onSlide}
			aria-describedby="shield-delay"
		/>
		<output for="delay" class="num">{delay.delaySeconds.toFixed(1)} s</output>
		<button
			class="btn"
			type="button"
			title="Tap the moment the lights go out on your TV"
			disabled={lightsOutUtc === null}
			onclick={onTap}><svg><use href="#i-lights" /></svg>Tap at lights out</button
		>
	</div>
	<div class="sync-meta">
		<span class="status-chip {status.tone}" data-testid="track-status"
			><i></i><span>{status.label}</span></span
		>
		<div class="clock"><span class="caps">Your clock</span><span class="num">{clock}</span></div>
		{#if !isLive}
			<div class="seg" role="group" aria-label="Replay speed">
				{#each SPEEDS as option (option)}
					<button type="button" aria-pressed={speed === option} onclick={() => onSpeed(option)}
						>{option}×</button
					>
				{/each}
			</div>
			<button class="btn" type="button" aria-label="Restart replay" onclick={onRestart}
				><svg><use href="#i-restart" /></svg><span>Restart</span></button
			>
		{/if}
	</div>
	<span class="sync-toast" role="status">{toast}</span>
</section>
