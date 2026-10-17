<!--
  The spoiler shield: the design's sticky Delay Sync strip. One control for
  the one clock everything on the page is gated by — the delay slider,
  tap-to-sync at lights out or as the leader crosses the line, pause and
  resume with the TV, watching a live race from its start (ROADMAP W6), how
  far to trust the sync, the track status, and (replays only) speed and
  restart. It is also the full-width flag banner (W4): tinted for a yellow,
  safety car, VSC or red flag, from the released track status only.
-->
<script lang="ts">
	import {
		serverNowMs,
		syncQuality,
		type DelaySyncController,
		type DelayState,
		type SyncQuality
	} from '$lib/delay-sync';
	import type { TrackStatusChip } from './race-feed';
	import WakeToggle from './WakeToggle.svelte';

	interface Props {
		controller: DelaySyncController;
		isLive: boolean;
		/** A race or sprint: the leader's crossings mean something. */
		isRace: boolean;
		speed: number;
		status: TrackStatusChip;
		/** The instant lights went out (event time), for tap-to-sync. */
		lightsOutUtc: string | null;
		/** The race's own start status only (never a stand-in), for catch-up. */
		raceStartUtc: string | null;
		onSpeed: (speed: number) => void;
		onRestart: () => void;
		/** Rebuild the page from lights out, `delaySeconds` behind live. */
		onCatchUp: () => void;
	}

	let {
		controller,
		isLive,
		isRace,
		speed,
		status,
		lightsOutUtc,
		raceStartUtc,
		onSpeed,
		onRestart,
		onCatchUp
	}: Props = $props();

	const SPEEDS = [1, 4, 16] as const;

	let delay = $state<DelayState>({
		delaySeconds: 0,
		synced: false,
		paused: false,
		method: 'default',
		setAtMs: null,
		anchorLap: null
	});
	$effect(() => controller.delayState.subscribe((value) => (delay = value)));

	/** Phones show the shield and the flag on one line; the controls open
	 * on demand so the race, not the strip, fills the screen (W7). Wider
	 * screens always show them. */
	let open = $state(false);
	let toast = $state('');
	let toastTimer: ReturnType<typeof setTimeout> | null = null;

	/** Bumps on every flag change after the first, to sweep it in (W2 D4). */
	let sweep = $state(0);
	let lastTone: string | null = null;
	$effect(() => {
		const tone = status.tone;
		if (lastTone !== null && tone !== lastTone) sweep++;
		lastTone = tone;
	});

	let clock = $state('');
	let quality = $state<SyncQuality>({ level: 'unsynced', label: 'Spoiler shield on', detail: '' });
	let canLeader = $state(false);
	$effect(() => {
		const tick = () => {
			const now = serverNowMs();
			const clockMs = controller.clockMs(now);
			clock = new Date(clockMs).toLocaleTimeString([], { hour12: false });
			quality = syncQuality({
				delay: controller.delayState.get(),
				nowMs: now,
				clockMs,
				newestReceivedMs: controller.newestReceivedMs,
				isLive
			});
			canLeader = controller.hasLeaderCrossings;
		};
		tick();
		const handle = setInterval(tick, 500);
		return () => clearInterval(handle);
	});

	/** "12.0 s", or past the slider "1 h 04 min" / "8 min 20 s". */
	function behind(seconds: number): string {
		if (seconds <= controller.maxDelaySeconds) return `${seconds.toFixed(1)} s`;
		const h = Math.floor(seconds / 3600);
		const m = Math.floor((seconds % 3600) / 60);
		const s = Math.floor(seconds % 60);
		return h > 0 ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min ${s} s`;
	}

	function say(text: string): void {
		toast = text;
		if (toastTimer) clearTimeout(toastTimer);
		toastTimer = setTimeout(() => (toast = ''), 4000);
	}

	function onSlide(event: Event): void {
		controller.setDelaySeconds(Number((event.currentTarget as HTMLInputElement).value));
	}

	function onTap(): void {
		if (lightsOutUtc === null) return;
		// The server's clock, like the lights-out timestamp it is compared with.
		const seconds = controller.calibrateFromTap(serverNowMs(), lightsOutUtc);
		say(`Synced: ${seconds.toFixed(1)} s behind live`);
	}

	function onLeader(): void {
		const picked = controller.resyncOnLeader(serverNowMs());
		say(
			picked
				? `Synced to the leader finishing lap ${picked.lap}: ${behind(picked.delaySeconds)} behind live`
				: 'No crossing near enough: line up at lights out or with the slider first'
		);
	}

	function onPause(): void {
		if (delay.paused) controller.resume();
		else controller.pause();
	}
</script>

<section class="sync flag-{status.tone}" class:open aria-label="Delay Sync">
	{#if sweep > 0}{#key sweep}<span class="flag-sweep {status.tone}" aria-hidden="true"
			></span>{/key}{/if}
	<div class="shield" data-quality={quality.level} title={quality.detail}>
		<span class="shield-icon"><svg width="16" height="16"><use href="#i-shield" /></svg></span>
		<div class="shield-text">
			<strong data-testid="sync-quality">{quality.label}</strong>
			<span
				><span class="num" data-testid="shield-delay">{behind(delay.delaySeconds)}</span>
				behind live<span class="long">&nbsp;· nothing past your clock is shown</span></span
			>
		</div>
	</div>
	<button
		class="btn pause-btn"
		type="button"
		aria-pressed={delay.paused}
		data-testid="sync-pause"
		aria-label={delay.paused ? 'Resume with your TV' : 'Pause with your TV'}
		title={delay.paused ? 'Resume with your TV' : 'Pause with your TV'}
		onclick={onPause}
		>{#if delay.paused}<svg viewBox="0 0 16 16" aria-hidden="true"
				><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg
			><span>Resume</span>{:else}<svg viewBox="0 0 16 16" aria-hidden="true"
				><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor" /></svg
			><span>Pause</span>{/if}</button
	>
	<button
		class="btn sync-toggle"
		type="button"
		aria-expanded={open}
		aria-controls="sync-controls"
		onclick={() => (open = !open)}>{open ? 'Done' : 'Adjust'}</button
	>
	<div class="delay-ctl" id="sync-controls">
		<label for="delay" class="caps">Delay</label>
		<input
			type="range"
			id="delay"
			min={controller.minDelaySeconds}
			max={controller.maxDelaySeconds}
			step="0.5"
			value={Math.min(delay.delaySeconds, controller.maxDelaySeconds)}
			oninput={onSlide}
			aria-describedby="shield-delay"
		/>
		<output for="delay" class="num">{behind(delay.delaySeconds)}</output>
		<button
			class="btn"
			type="button"
			title="Tap the moment the lights go out on your TV"
			disabled={lightsOutUtc === null}
			onclick={onTap}><svg><use href="#i-lights" /></svg>Tap at lights out</button
		>
		{#if isRace}
			<button
				class="btn"
				type="button"
				data-testid="sync-leader"
				title="Tap the moment the leader crosses the line on your TV"
				disabled={!canLeader}
				onclick={onLeader}>Leader crossed the line</button
			>
		{/if}
		{#if isLive}
			<button
				class="btn"
				type="button"
				data-testid="sync-catch-up"
				title="Watching from the start? Press as the lights go out on your recording"
				disabled={raceStartUtc === null}
				onclick={onCatchUp}>Watching from the start</button
			>
		{/if}
		<span class="quality-detail" data-testid="sync-quality-detail">{quality.detail}</span>
	</div>
	<div class="sync-meta">
		<span class="status-chip {status.tone}" data-testid="track-status"
			><i></i><span>{status.label}</span></span
		>
		<div class="clock">
			<span class="caps">{delay.paused ? 'Paused at' : 'Your clock'}</span><span
				class="num"
				data-testid="sync-clock">{clock}</span
			>
		</div>
		<WakeToggle />
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

<style>
	.pause-btn svg {
		width: 14px;
		height: 14px;
	}
	/* A phone keeps the folded strip to one line (W7): the icon alone. */
	@media (max-width: 899px) {
		.pause-btn {
			min-width: 44px;
			justify-content: center;
			padding-inline: 0;
		}
		.pause-btn span {
			display: none;
		}
	}
	.pause-btn[aria-pressed='true'] {
		border-color: var(--warn);
	}
	/* On a wide screen the reason is the shield's tooltip, so the sticky
	   strip keeps its one line; a phone shows it with the controls. */
	.quality-detail {
		display: none;
		flex-basis: 100%;
		font-size: 12px;
		color: var(--muted);
	}
	@media (max-width: 899px) {
		.quality-detail {
			display: block;
		}
	}
	.shield[data-quality='good'] :global(.shield-icon) {
		color: var(--good);
	}
	.shield[data-quality='check'] :global(.shield-icon),
	.shield[data-quality='behind'] :global(.shield-icon),
	.shield[data-quality='paused'] :global(.shield-icon) {
		color: var(--warn);
	}
</style>
