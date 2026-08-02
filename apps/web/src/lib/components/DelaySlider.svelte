<script lang="ts">
	/** Manual calibration half of Delay Sync's UX (CLAUDE.md's Phase 3.3): a
	 * 0-120s delay slider with a live readout. Every drag goes straight
	 * through `DelaySyncController.setDelaySeconds`, which clamps and marks
	 * the state synced — this component holds no delay state of its own. */
	import type { DelaySyncController } from '$lib/delay-sync';

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();
	let delayStore = $derived(controller.delayState);

	function onInput(event: Event): void {
		const input = event.currentTarget as HTMLInputElement;
		controller.setDelaySeconds(Number(input.value));
	}
</script>

<label class="delay-slider">
	<span class="slider-label">Broadcast delay</span>
	<input
		type="range"
		min={controller.minDelaySeconds}
		max={controller.maxDelaySeconds}
		step="1"
		value={$delayStore.delaySeconds}
		oninput={onInput}
	/>
	<span class="slider-readout font-mono">{$delayStore.delaySeconds.toFixed(0)}s</span>
</label>

<style>
	.delay-slider {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.slider-label {
		font-size: var(--font-size-sm);
		color: var(--color-text-muted);
		white-space: nowrap;
	}

	input[type='range'] {
		flex: 1;
		accent-color: var(--color-accent);
	}

	.slider-readout {
		min-width: 3.5ch;
		text-align: right;
		font-weight: 600;
	}
</style>
