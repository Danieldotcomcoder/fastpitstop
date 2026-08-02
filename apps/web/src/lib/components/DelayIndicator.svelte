<script lang="ts">
	/** The "so the user trusts it" half of Delay Sync's calibration UX
	 * (CLAUDE.md's Phase 3.3): a small always-visible readout of the current
	 * delay and whether it has actually been calibrated, vs still sitting at
	 * the untouched default. Purely a view over `DelaySyncController.delayState`
	 * — it never reads the buffer or decides anything itself. */
	import type { DelaySyncController } from '$lib/delay-sync';

	interface Props {
		controller: DelaySyncController;
	}

	let { controller }: Props = $props();
	let delayStore = $derived(controller.delayState);
</script>

<span class="indicator" class:synced={$delayStore.synced}>
	<span class="dot" aria-hidden="true"></span>
	<span class="state">{$delayStore.synced ? 'Synced' : 'Unsynced'}</span>
	<span class="value font-mono">{$delayStore.delaySeconds.toFixed(0)}s delay</span>
</span>

<style>
	.indicator {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		padding: 0.1875rem var(--space-3);
		border-radius: 999px;
		border: 1px solid var(--color-border-strong);
		font-size: var(--font-size-xs);
		font-weight: 600;
		line-height: 1;
		white-space: nowrap;
		color: var(--color-text-muted);
	}

	.dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 50%;
		flex: none;
		background: var(--color-confidence-thin);
	}

	.synced .dot {
		background: var(--color-confidence-good);
	}

	.synced {
		color: var(--color-confidence-good);
		border-color: color-mix(in srgb, var(--color-confidence-good) 45%, var(--color-border));
	}

	.state {
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.value {
		color: var(--color-text);
		font-weight: 600;
	}
</style>
