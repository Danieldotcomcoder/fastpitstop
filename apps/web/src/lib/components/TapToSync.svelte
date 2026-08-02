<script lang="ts">
	/** Auto-calibration half of Delay Sync's UX (CLAUDE.md's Phase 3.3):
	 * "tap when the lights go out" (or tap on whatever incident the caller
	 * wired up as the sync anchor). Disabled until `anchorEventTimeUtc` is
	 * known — there is nothing to compare a tap against before then. All the
	 * math lives in `DelaySyncController.calibrateFromTap`; this component
	 * only reads `Date.now()` at the moment of the click. */
	import type { DelaySyncController } from '$lib/delay-sync';

	interface Props {
		controller: DelaySyncController;
		/** `event_time_utc` of the moment being tapped for. `null` while
		 * nothing to calibrate against has arrived yet. */
		anchorEventTimeUtc: string | null;
		label?: string;
	}

	let { controller, anchorEventTimeUtc, label = 'Tap when lights go out' }: Props = $props();

	function handleTap(): void {
		if (anchorEventTimeUtc === null) return;
		controller.calibrateFromTap(Date.now(), anchorEventTimeUtc);
	}
</script>

<button
	type="button"
	class="tap-to-sync"
	onclick={handleTap}
	disabled={anchorEventTimeUtc === null}
>
	{label}
</button>

<style>
	.tap-to-sync {
		padding: var(--space-2) var(--space-4);
		border-radius: var(--radius-md);
		border: 1px solid var(--color-border-strong);
		background: var(--color-bg-elevated);
		font-size: var(--font-size-sm);
		font-weight: 600;
		cursor: pointer;
	}

	.tap-to-sync:hover:not(:disabled) {
		border-color: var(--color-accent);
		color: var(--color-accent-strong);
	}

	.tap-to-sync:disabled {
		color: var(--color-text-faint);
		cursor: not-allowed;
	}
</style>
