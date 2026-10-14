<!--
  "Keep screen on" (ROADMAP W7): a phone beside the TV should not dim
  mid-stint. Shown only where the browser supports the Screen Wake Lock
  API; the choice is remembered on this device.
-->
<script lang="ts">
	import { onDestroy } from 'svelte';
	import {
		ScreenWake,
		rememberWake,
		rememberedWake,
		type WakeLockApi,
		type WakeState
	} from '$lib/wake-lock';

	const api =
		typeof navigator !== 'undefined' && 'wakeLock' in navigator
			? (navigator.wakeLock as unknown as WakeLockApi)
			: null;
	const wake = api ? new ScreenWake(api, document) : null;
	let state = $state<WakeState>({ on: false, held: false });

	if (wake) {
		wake.subscribe((value) => (state = value));
		if (rememberedWake()) void wake.set(true);
	}
	onDestroy(() => void wake?.dispose());

	function toggle(): void {
		if (!wake) return;
		const on = !state.on;
		rememberWake(on);
		void wake.set(on);
	}
</script>

{#if wake}
	<button
		class="btn wake-toggle"
		type="button"
		aria-pressed={state.on}
		title={state.on && !state.held ? 'Your browser is not keeping the screen on right now' : ''}
		onclick={toggle}
		data-testid="wake-toggle"
		><svg><use href="#i-sun" /></svg><span>{state.on ? 'Screen stays on' : 'Keep screen on'}</span
		></button
	>
{/if}
