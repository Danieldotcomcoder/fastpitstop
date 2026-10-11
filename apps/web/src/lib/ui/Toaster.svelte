<!--
  Renders a ToastQueue in the corner (bottom on phones, above the tab bar).
  Announced politely to screen readers; each toast can be dismissed.
-->
<script lang="ts">
	import { onDestroy } from 'svelte';
	import type { Toast, ToastQueue } from './toasts';

	interface Props {
		queue: ToastQueue;
		/** Bump to re-read the queue straight after a push (otherwise it is
		 * re-read every half second). */
		revision?: number;
		now?: () => number;
	}

	let { queue, revision = 0, now = () => Date.now() }: Props = $props();

	let tick = $state(0);
	const timer = setInterval(() => tick++, 500);
	onDestroy(() => clearInterval(timer));

	let shown = $state<readonly Toast[]>([]);
	$effect(() => {
		void revision;
		void tick;
		shown = [...queue.visible(now())];
	});

	function dismiss(id: number): void {
		queue.dismiss(id);
		shown = [...queue.visible(now())];
	}
</script>

<div class="toaster" role="status" aria-live="polite">
	{#each shown as toast (toast.id)}
		<div class="toast">
			<div class="text">
				{#if toast.kicker}<span class="kicker">{toast.kicker}</span>{/if}
				<span>{toast.text}</span>
			</div>
			<button type="button" onclick={() => dismiss(toast.id)} aria-label="Dismiss">✕</button>
		</div>
	{/each}
</div>

<style>
	.toaster {
		position: fixed;
		right: var(--space-4);
		bottom: calc(var(--space-4) + env(safe-area-inset-bottom));
		z-index: 30;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		width: min(360px, calc(100vw - 2 * var(--space-4)));
		pointer-events: none;
	}
	@media (max-width: 639px) {
		.toaster {
			bottom: calc(64px + env(safe-area-inset-bottom));
		}
	}
	.toast {
		pointer-events: auto;
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
		padding: var(--space-3);
		border-radius: var(--radius-md);
		background: var(--board);
		color: var(--board-ink);
		box-shadow: var(--elev-3);
		font-size: var(--font-size-sm);
		line-height: 1.4;
	}
	.text {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.kicker {
		font: 500 11px / 1.2 var(--font-mono);
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--board-hi);
	}
	button {
		min-width: 32px;
		min-height: 32px;
		border: 0;
		background: none;
		color: var(--board-muted);
		cursor: pointer;
	}
	@media (prefers-reduced-motion: no-preference) {
		.toast {
			animation: slide var(--duration-move) var(--ease-out);
		}
	}
	@keyframes slide {
		from {
			transform: translateY(12px);
			opacity: 0;
		}
	}
</style>
